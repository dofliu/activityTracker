"""ADR-008 gated executor（P5-R2 L0/L1 in-process；P5-R3 L2 dispatcher）。

安全契約落地：

- **D1** execute 只接受 ``proposal_id``（L2 另加 server 產生的 confirm
  code，與在多動作 proposal 中選擇已註冊 template 的 ``template_id``）；
  proposal 由 server 端即時重建（deterministic id），evidence 已改變的
  建議自動失效，永不執行過期提案。呼叫端仍然無法提供任何 command、
  path 或 argv。
- **D2** 動作來自程式碼註冊的白名單 template：L0/L1 為內部函式呼叫
  （重用 ADR-011 repo_sync、handoff_engine、open-loop lifecycle）；
  P5-R3 的 L2 template 經 ``core.agent_dispatch`` 以 argv-list（禁 shell）
  調度本機 agent CLI，cwd 限已探索 repo root、環境變數 allowlist 重建。
- **D3** L0 唯讀可直接執行、L1 需使用者單鍵批准（HTTP 呼叫本身）＋
  execution token；L2 需 l2.enabled、一次性 6 碼 confirm code
  （預設 5 分鐘失效、單次有效）與每 template 冷卻時間，缺一即拒。
- **D4** token 驗證在 server 層（``security.execution_authorized``）。
- **D5** 每次執行寫入 ``agent_execution_receipts``（migration 014）；
  receipt 只含白名單摘要欄位與 output digest，不含內容全文或 secrets。
- **D6** 任何驗證失敗 → 拒絕；executor 總開關預設關閉、L2 另有獨立
  開關且同樣預設關閉，關閉時行為分別回到 ADR-007 / P5-R2 樣態。

L0/L1 為請求內同步呼叫並受硬性 timeout；逾時的執行緒無法被中斷
（in-process），receipt 如實標記 ``timeout``，cancel 只對 ``queued``
有效。L2 dispatcher job 則登記 OS 行程，逾時會真正 kill，執行中也可
由 cancel endpoint 中止（``cancelled`` 為一級狀態）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets as py_secrets
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from core.agent_dispatch import (
    DispatchRejected,
    DispatchTimeout,
    is_running_registered,
    kill_running,
    run_agent_subprocess,
)
from core.config import get_config
from core.database import get_db
from core.models import AgentExecutionReceipt
from core.runtime_paths import runtime_data_root
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.AgentExecutor")

RISK_L0 = "L0_READ_ONLY"
RISK_L1 = "L1_ASSIST"
RISK_L2 = "L2_MUTATE"
ACTIVE_STATUSES = ("queued", "running")

EXECUTOR_CLAIM_BOUNDARY = (
    "Executor runs only server-registered whitelist templates against a live "
    "proposal_id; it never accepts caller-provided commands, paths or argv, "
    "and every run leaves an audit receipt."
)

RESPONSE_TEXT_LIMIT = 20000

# L2 confirm code：一次性、短效；只存在 server 記憶體，不落庫、不進 log。
_PENDING_L2_CONFIRMS: dict[str, dict[str, Any]] = {}


class ExecutionRejected(RuntimeError):
    """Fail-closed 拒絕；error_code 穩定、message 不含 secrets。"""

    def __init__(self, error_code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


class AgentCliFailed(RuntimeError):
    """L2 CLI 以非零 exit code 結束；payload 保留非敏感輸出統計供 receipt。"""

    def __init__(self, exit_code: int, payload: dict[str, Any]):
        super().__init__(f"agent CLI exited with {exit_code}")
        self.exit_code = exit_code
        self.payload = payload


@dataclass(frozen=True)
class ActionPlan:
    """derive 階段的結果：display 與 execute 共用同一份，確保一致。

    ``runner`` 接收 execution context（目前只含 ``receipt_id``），讓
    dispatcher 類 template 能把 OS 行程登記到 cancel registry。
    """

    template_id: str
    risk_level: str
    label: str
    call_description: str
    params: dict[str, Any]
    timeout_seconds: int
    receipt_fields: tuple[str, ...]
    runner: Callable[[dict[str, Any]], dict[str, Any]]
    dispatch_mode: str = "in_process"
    # 執行前置檢查（如 clean worktree）：在發放 confirm code 之前執行，
    # 失敗即拒絕、不進入確認流程；runner 內仍需自行再檢查一次。
    precheck: Callable[[], None] | None = None


@dataclass
class ExecutorServices:
    """P5-R2 白名單動作依賴的內部服務；測試可注入替身。"""

    repo_references: Callable[[], list[Any]] = field(default=None)  # type: ignore[assignment]
    repo_execute: Callable[[str, str], dict[str, Any]] = field(default=None)  # type: ignore[assignment]
    build_handoff: Callable[[str], dict[str, Any]] = field(default=None)  # type: ignore[assignment]
    format_handoff: Callable[[dict[str, Any]], str] = field(default=None)  # type: ignore[assignment]
    loop_transition: Callable[[int, str, str | None], dict[str, Any]] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.repo_references is None or self.repo_execute is None:
            from core.repo_sync import LocalRepositorySync

            sync = LocalRepositorySync()
            if self.repo_references is None:
                # 只做 .git 目錄探索（輕量），不跑 git status。
                self.repo_references = lambda: sync._discover_references()[0]
            if self.repo_execute is None:
                self.repo_execute = lambda repo_id, action: sync.execute(repo_id, action)
        if self.build_handoff is None or self.format_handoff is None:
            from core.handoff_engine import build_project_handoff, format_handoff_markdown

            if self.build_handoff is None:
                self.build_handoff = build_project_handoff
            if self.format_handoff is None:
                self.format_handoff = format_handoff_markdown
        if self.loop_transition is None:
            from core.project_engine import transition_open_loop

            self.loop_transition = transition_open_loop


def executor_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("proactive_secretary.executor.enabled", False))


def l2_enabled(cfg: Any | None = None) -> bool:
    """L2_MUTATE 獨立開關；預設關閉，且必須疊加在 executor 總開關之上。"""
    cfg = cfg or get_config()
    return executor_enabled(cfg) and bool(
        cfg.get("proactive_secretary.executor.l2.enabled", False)
    )


def l2_write_enabled(cfg: Any | None = None) -> bool:
    """ADR-008 Addendum A2：寫入型 L2 template 的第三開關，預設關閉。"""
    cfg = cfg or get_config()
    return l2_enabled(cfg) and bool(
        cfg.get("proactive_secretary.executor.l2.allow_write", False)
    )


def _l2_confirm_ttl_seconds(cfg: Any) -> int:
    try:
        raw = int(cfg.get("proactive_secretary.executor.l2.confirm_ttl_seconds", 300))
    except (TypeError, ValueError):
        return 300
    return min(900, max(30, raw))


def _l2_cooldown_seconds(cfg: Any) -> int:
    try:
        raw = int(cfg.get("proactive_secretary.executor.l2.cooldown_seconds", 600))
    except (TypeError, ValueError):
        return 600
    return min(24 * 3600, max(0, raw))


_PR_ISSUE_TYPES = {
    "ci_failing_pr",
    "review_ready_pr",
    "aging_pr",
    "assigned_issue",
    "aging_issue",
}


def _single_open_loop_id(proposal: dict[str, Any]) -> int | None:
    # triage_signals 的 evidence ref 格式為 open_loops:<id>（複數）。
    loop_ids = [
        ref.split(":", 1)[1]
        for ref in proposal.get("evidence_refs", [])
        if isinstance(ref, str) and ref.startswith("open_loops:")
    ]
    if len(loop_ids) != 1:
        return None
    try:
        return int(loop_ids[0])
    except ValueError:
        return None


def _matching_repo(project_key: str, references: list[Any]):
    matches = [ref for ref in references if ref.path.name == project_key]
    # 同名多個本機 clone 屬歧義，fail-closed 不提供執行。
    return matches[0] if len(matches) == 1 else None


_REPO_SYNC_TYPES = {"repo_needs_pull", "repo_needs_push"}
# 這些提案沒有「專案」可產 Handoff：extension 是系統本身；no_daily_routine 的
# project_key 只是佔位（ADR-017）。
_NO_HANDOFF_TYPES = {"verify_extension_heartbeat", "no_daily_routine"}


def _repo_id_from_subject(proposal: dict[str, Any]) -> str | None:
    """Repo 同步提案以 ``repo:<repo_id>`` 指涉目標；只接受既有探索結果內的 id。"""
    subject = str(proposal.get("subject_ref") or "")
    if not subject.startswith("repo:"):
        return None
    repo_id = subject.split(":", 1)[1]
    return repo_id if len(repo_id) == 16 and all(ch in "0123456789abcdef" for ch in repo_id) else None


def derive_action(
    proposal: dict[str, Any],
    *,
    services: ExecutorServices,
) -> ActionPlan | None:
    """每個 proposal 對應至多一個 deterministic template；display 與 execute 共用。"""
    proposal_type = str(proposal.get("proposal_type") or "")
    project_key = str(proposal.get("project_key") or "")

    if proposal_type in _REPO_SYNC_TYPES:
        repo_id = _repo_id_from_subject(proposal)
        try:
            references = services.repo_references()
        except Exception:  # noqa: BLE001 — 探索失敗視為不可執行
            references = []
        if repo_id is None or not any(ref.repo_id == repo_id for ref in references):
            return None
        if proposal_type == "repo_needs_pull":
            # L1：只在 clean worktree、只落後且可 fast-forward 時才會真的執行，
            # repo_sync.execute 會在 lock 內重檢；不符即 failed receipt，不 force。
            return ActionPlan(
                template_id="repo_pull_ff",
                risk_level=RISK_L1,
                label=f"本機 {project_key} fast-forward pull（clean 且只落後時才執行）",
                call_description=f"repo_sync.execute({repo_id!r}, 'pull_ff_only')",
                params={"repo_id": repo_id, "action": "pull_ff_only"},
                timeout_seconds=180,
                receipt_fields=("repo_name", "action", "status", "return_code"),
                runner=lambda _ctx: _safe_repo_receipt(
                    services.repo_execute(repo_id, "pull_ff_only")
                ),
            )
        # repo_needs_push：push 留在同步中心逐一／批次確認，這裡只提供 fetch 讓判斷更新。
        return ActionPlan(
            template_id="repo_fetch",
            risk_level=RISK_L1,
            label=f"更新本機 {project_key} 的 remote-tracking（git fetch）；push 請到同步中心確認",
            call_description=f"repo_sync.execute({repo_id!r}, 'fetch')",
            params={"repo_id": repo_id, "action": "fetch"},
            timeout_seconds=120,
            receipt_fields=("repo_name", "action", "status", "return_code"),
            runner=lambda _ctx: _safe_repo_receipt(
                services.repo_execute(repo_id, "fetch")
            ),
        )

    if proposal_type in _PR_ISSUE_TYPES and project_key:
        try:
            references = services.repo_references()
        except Exception:  # noqa: BLE001 — 探索失敗視為不可執行，不阻擋顯示
            references = []
        repo = _matching_repo(project_key, references)
        if repo is not None:
            repo_id = repo.repo_id
            return ActionPlan(
                template_id="repo_fetch",
                risk_level=RISK_L1,
                label=f"更新本機 {project_key} 的 remote-tracking（git fetch）",
                call_description=f"repo_sync.execute({repo_id!r}, 'fetch')",
                params={"repo_id": repo_id, "action": "fetch"},
                timeout_seconds=120,
                receipt_fields=("repo_name", "action", "status", "return_code"),
                runner=lambda _ctx: _safe_repo_receipt(
                    services.repo_execute(repo_id, "fetch")
                ),
            )

    if proposal_type == "stalled_open_loop":
        loop_id = _single_open_loop_id(proposal)
        if loop_id is not None:
            return ActionPlan(
                template_id="open_loop_mark_stale",
                risk_level=RISK_L1,
                label="將此未結事項標記為 stale（可用 open 復原）",
                call_description=f"project_engine.transition_open_loop({loop_id}, 'stale')",
                params={"loop_id": loop_id, "status": "stale"},
                timeout_seconds=30,
                receipt_fields=("loop_id", "status"),
                runner=lambda _ctx: _loop_receipt(
                    services.loop_transition(loop_id, "stale", "via secretary executor"),
                    loop_id,
                ),
            )

    # ADR-017：no_daily_routine 的 project_key 只是「OmniContext」這個佔位，為它產
    # Handoff 沒有意義；其餘帶專案的提案（含 neglected_active_project）都給 L0 Handoff。
    if project_key and proposal_type not in _NO_HANDOFF_TYPES:
        return ActionPlan(
            template_id="generate_handoff",
            risk_level=RISK_L0,
            label=f"產生 {project_key} 的 Context Handoff（唯讀）",
            call_description=f"handoff_engine.build_project_handoff({project_key!r})",
            params={"project_key": project_key},
            timeout_seconds=60,
            receipt_fields=("project_key", "handoff_chars"),
            runner=lambda _ctx: _handoff_receipt(services, project_key),
        )

    return None


# ---- P5-R3：L2 subprocess template（調度本機 agent CLI） ----

_DRAFT_PLAN_TYPES = {"stalled_open_loop", "unfinished_recent"}
_DRAFT_PROMPT_FIELD_LIMITS = {"title": 120, "detail": 300, "suggested_action": 200}


def _draft_prompt(proposal: dict[str, Any]) -> str:
    """server 端組 prompt；只用白名單欄位並截斷，呼叫端無法注入內容。"""
    parts = {
        key: str(proposal.get(key) or "").replace("\n", " ")[:limit]
        for key, limit in _DRAFT_PROMPT_FIELD_LIMITS.items()
    }
    project_key = str(proposal.get("project_key") or "")[:80]
    return (
        "你是唯讀顧問。針對以下停滯的工作事項，起草一份簡短的重啟行動計畫"
        "（繁體中文，最多 40 行，條列步驟與第一步的具體切入點）。"
        "只輸出計畫本身；不要修改任何檔案、不要執行任何工具或命令。\n"
        f"專案：{project_key}\n"
        f"事項：{parts['title']}\n"
        + (f"細節：{parts['detail']}\n" if parts["detail"] else "")
        + (f"原有建議：{parts['suggested_action']}\n" if parts["suggested_action"] else "")
    )


def _agent_cli_settings(cfg: Any) -> tuple[str, list[str], int]:
    binary = str(cfg.get("proactive_secretary.executor.agent_cli.binary", "claude") or "claude")
    raw_args = cfg.get("proactive_secretary.executor.agent_cli.args", ["-p", "{prompt}"])
    if not isinstance(raw_args, (list, tuple)):
        raw_args = ["-p", "{prompt}"]
    args = [str(item) for item in raw_args]
    try:
        timeout = int(cfg.get("proactive_secretary.executor.agent_cli.timeout_seconds", 240))
    except (TypeError, ValueError):
        timeout = 240
    return binary, args, min(1800, max(30, timeout))


def _run_agent_draft(
    ctx: dict[str, Any],
    *,
    argv: list[str],
    cwd: str,
    timeout_seconds: int,
    project_key: str,
    binary: str,
) -> dict[str, Any]:
    receipt_id = ctx.get("receipt_id")
    outcome = run_agent_subprocess(
        argv, cwd=cwd, timeout_seconds=timeout_seconds, receipt_id=receipt_id
    )
    stdout = str(outcome.get("stdout") or "").strip()
    payload: dict[str, Any] = {
        "project_key": project_key,
        "binary": binary,
        "exit_code": outcome.get("exit_code"),
        "output_chars": len(stdout),
        "output_path": None,
    }
    if outcome.get("exit_code") != 0:
        logger.warning(
            "Agent CLI %s exited %s: %s",
            binary,
            outcome.get("exit_code"),
            str(outcome.get("stderr") or "")[:200],
        )
        raise AgentCliFailed(int(outcome.get("exit_code") or -1), payload)

    if stdout:
        output_dir = runtime_data_root() / "agent_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"execution_{receipt_id or 'adhoc'}.md"
        output_path.write_text(stdout, encoding="utf-8")
        payload["output_path"] = str(output_path)
    payload["plan_markdown"] = stdout[:RESPONSE_TEXT_LIMIT]
    payload["stdout_truncated"] = bool(outcome.get("stdout_truncated"))
    return payload


def _maybe_agent_draft_plan(
    proposal: dict[str, Any],
    *,
    services: ExecutorServices,
    cfg: Any,
) -> ActionPlan | None:
    """L2：調度本機 agent CLI 為停滯事項起草計畫；沒有唯一 repo 即不提供。"""
    if str(proposal.get("proposal_type") or "") not in _DRAFT_PLAN_TYPES:
        return None
    project_key = str(proposal.get("project_key") or "")
    if not project_key:
        return None
    try:
        references = services.repo_references()
    except Exception:  # noqa: BLE001 — 探索失敗視為不可執行
        references = []
    repo = _matching_repo(project_key, references)
    if repo is None:
        return None

    binary, args, timeout_seconds = _agent_cli_settings(cfg)
    prompt = _draft_prompt(proposal)
    argv = [binary] + [item.replace("{prompt}", prompt) for item in args]
    if not any("{prompt}" in item for item in args):
        argv.append(prompt)
    cwd = str(repo.path)

    return ActionPlan(
        template_id="agent_draft_plan",
        risk_level=RISK_L2,
        label=f"調度本機 {binary} 為此事項起草行動計畫（唯讀輸出，消耗 CLI 額度）",
        call_description=f"agent_dispatch.run({binary}, cwd={project_key!r})",
        params={"project_key": project_key, "binary": binary, "cwd": cwd},
        timeout_seconds=timeout_seconds,
        receipt_fields=(
            "project_key",
            "binary",
            "exit_code",
            "output_chars",
            "output_path",
        ),
        runner=lambda ctx: _run_agent_draft(
            ctx,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            project_key=project_key,
            binary=binary,
        ),
        dispatch_mode="subprocess",
    )


# ---- ADR-008 Addendum：L2 寫入型 template（agent 依已批准計畫實際改檔） ----

_DRAFT_RECEIPT_MAX_AGE_HOURS = 24
_APPLY_PLAN_TEXT_LIMIT = 6000
_APPLY_PROMPT_HEADER = (
    "你是代辦執行者。以下是使用者已批准的行動計畫，請在目前的 repo 工作目錄內執行它。\n"
    "約束：只能修改此 repo 內的檔案；不要執行 git commit、git push 或任何版本控制寫入；"
    "不要碰 repo 以外的路徑。完成後輸出：做了什麼、改了哪些檔案、還剩什麼未完成。\n"
    "【已批准的計畫】\n"
)


def _agent_cli_write_settings(cfg: Any) -> tuple[str, list[str], int] | None:
    """寫入模式的 CLI 參數；未知 binary 又沒明示 write_args 時 fail-closed 不提供。"""
    binary = str(cfg.get("proactive_secretary.executor.agent_cli.binary", "claude") or "claude")
    defaults = {
        "claude": ["-p", "{prompt}", "--permission-mode", "acceptEdits"],
        "codex": ["exec", "--full-auto", "{prompt}"],
    }
    raw_args = cfg.get("proactive_secretary.executor.agent_cli.write_args", None)
    if not isinstance(raw_args, (list, tuple)) or not raw_args:
        raw_args = defaults.get(binary)
        if raw_args is None:
            return None
    try:
        timeout = int(cfg.get("proactive_secretary.executor.agent_cli.write_timeout_seconds", 600))
    except (TypeError, ValueError):
        timeout = 600
    return binary, [str(item) for item in raw_args], min(3600, max(60, timeout))


def _porcelain_lines(cwd: str) -> list[str]:
    outcome = run_agent_subprocess(
        ["git", "status", "--porcelain"], cwd=cwd, timeout_seconds=30
    )
    if outcome.get("exit_code") != 0:
        raise DispatchRejected("git_status_failed", "無法確認 worktree 狀態")
    return [line for line in str(outcome.get("stdout") or "").splitlines() if line.strip()]


def _clean_worktree_precheck(cwd: str) -> Callable[[], None]:
    def _check() -> None:
        try:
            dirty = _porcelain_lines(cwd)
        except DispatchRejected as exc:
            raise ExecutionRejected(exc.error_code, str(exc)) from exc
        if dirty:
            raise ExecutionRejected(
                "worktree_not_clean",
                f"repo 有 {len(dirty)} 筆未提交變更；請先 commit 或 stash，"
                "避免 agent 的修改與您的工作混在一起",
            )

    return _check


def _recent_draft_plan(
    project_key: str, *, database: Any, now: datetime
) -> tuple[int, str] | None:
    """回傳 (draft receipt id, 計畫全文)；沒有可引用的近期計畫即 None。"""
    window_start = now - timedelta(hours=_DRAFT_RECEIPT_MAX_AGE_HOURS)
    with database.session_scope() as session:
        row = (
            session.query(AgentExecutionReceipt)
            .filter(
                AgentExecutionReceipt.template_id == "agent_draft_plan",
                AgentExecutionReceipt.status == "succeeded",
                AgentExecutionReceipt.project_key == project_key,
                AgentExecutionReceipt.requested_at > window_start,
            )
            .order_by(AgentExecutionReceipt.requested_at.desc())
            .first()
        )
        if row is None or not row.output_summary:
            return None
        receipt_id = row.id
        try:
            output_path = json.loads(row.output_summary).get("output_path")
        except (ValueError, AttributeError):
            return None
    if not output_path:
        return None
    try:
        from pathlib import Path

        text = Path(output_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return (receipt_id, text) if text else None


def _run_agent_apply(
    ctx: dict[str, Any],
    *,
    argv: list[str],
    cwd: str,
    timeout_seconds: int,
    project_key: str,
    binary: str,
    plan_receipt_id: int,
) -> dict[str, Any]:
    # runner 內再驗一次 worktree（confirm 流程與執行之間可能有變化）。
    if _porcelain_lines(cwd):
        raise DispatchRejected(
            "worktree_not_clean", "repo 在確認流程期間出現未提交變更，已中止"
        )
    receipt_id = ctx.get("receipt_id")
    outcome = run_agent_subprocess(
        argv, cwd=cwd, timeout_seconds=timeout_seconds, receipt_id=receipt_id
    )
    try:
        changed_files = _porcelain_lines(cwd)
    except DispatchRejected:
        changed_files = []
    stdout = str(outcome.get("stdout") or "").strip()
    payload: dict[str, Any] = {
        "project_key": project_key,
        "binary": binary,
        "exit_code": outcome.get("exit_code"),
        "files_changed": len(changed_files),
        "plan_receipt_id": plan_receipt_id,
        "output_path": None,
    }
    if outcome.get("exit_code") != 0:
        raise AgentCliFailed(int(outcome.get("exit_code") or -1), payload)

    if stdout:
        output_dir = runtime_data_root() / "agent_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"execution_{receipt_id or 'adhoc'}.md"
        output_path.write_text(stdout, encoding="utf-8")
        payload["output_path"] = str(output_path)
    # 改動檔名只進當次回應（使用者當下檢視），不落 receipt（A4）。
    payload["changed_files"] = [line[3:] for line in changed_files][:50]
    payload["report_markdown"] = stdout[:RESPONSE_TEXT_LIMIT]
    payload["claim_boundary"] = (
        "Agent 的修改以未提交變更留在 worktree；請用 git diff 檢視後自行 commit，"
        "或以 git checkout . 整批還原。"
    )
    return payload


def _maybe_agent_apply_plan(
    proposal: dict[str, Any],
    *,
    services: ExecutorServices,
    cfg: Any,
    database: Any,
    now: datetime,
) -> ActionPlan | None:
    """A1 兩段式：只有存在近期已批准（succeeded）的 draft 計畫時才提供。"""
    if str(proposal.get("proposal_type") or "") not in _DRAFT_PLAN_TYPES:
        return None
    project_key = str(proposal.get("project_key") or "")
    if not project_key:
        return None
    settings = _agent_cli_write_settings(cfg)
    if settings is None:
        return None
    try:
        references = services.repo_references()
    except Exception:  # noqa: BLE001
        references = []
    repo = _matching_repo(project_key, references)
    if repo is None:
        return None
    draft = _recent_draft_plan(project_key, database=database, now=now)
    if draft is None:
        return None
    plan_receipt_id, plan_text = draft

    binary, write_args, timeout_seconds = settings
    prompt = _APPLY_PROMPT_HEADER + plan_text[:_APPLY_PLAN_TEXT_LIMIT]
    argv = [binary] + [item.replace("{prompt}", prompt) for item in write_args]
    if not any("{prompt}" in item for item in write_args):
        argv.append(prompt)
    cwd = str(repo.path)

    return ActionPlan(
        template_id="agent_apply_plan",
        risk_level=RISK_L2,
        label=(
            f"讓本機 {binary} 依已批准的計畫（receipt #{plan_receipt_id}）實際修改此 repo"
            "（不 commit，改動留給您檢視）"
        ),
        call_description=f"agent_dispatch.apply({binary}, cwd={project_key!r}, plan=#{plan_receipt_id})",
        params={
            "project_key": project_key,
            "binary": binary,
            "cwd": cwd,
            "plan_receipt_id": plan_receipt_id,
        },
        timeout_seconds=timeout_seconds,
        receipt_fields=(
            "project_key",
            "binary",
            "exit_code",
            "files_changed",
            "plan_receipt_id",
            "output_path",
        ),
        runner=lambda ctx: _run_agent_apply(
            ctx,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            project_key=project_key,
            binary=binary,
            plan_receipt_id=plan_receipt_id,
        ),
        dispatch_mode="subprocess",
        precheck=_clean_worktree_precheck(cwd),
    )


def derive_actions(
    proposal: dict[str, Any],
    *,
    services: ExecutorServices,
    cfg: Any | None = None,
    database: Any | None = None,
    now: datetime | None = None,
) -> list[ActionPlan]:
    """proposal 對應的全部已註冊動作；第一項為既有 primary（向後相容）。"""
    cfg = cfg or get_config()
    plans: list[ActionPlan] = []
    primary = derive_action(proposal, services=services)
    if primary is not None:
        plans.append(primary)
    if l2_enabled(cfg):
        extra = _maybe_agent_draft_plan(proposal, services=services, cfg=cfg)
        if extra is not None and all(
            plan.template_id != extra.template_id for plan in plans
        ):
            plans.append(extra)
        if l2_write_enabled(cfg):
            apply_plan = _maybe_agent_apply_plan(
                proposal,
                services=services,
                cfg=cfg,
                database=database or get_db(),
                now=now or get_local_now(),
            )
            if apply_plan is not None and all(
                plan.template_id != apply_plan.template_id for plan in plans
            ):
                plans.append(apply_plan)
    return plans


def _safe_repo_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_name": receipt.get("repo_name"),
        "action": receipt.get("action"),
        "status": receipt.get("status"),
        "return_code": receipt.get("return_code"),
        "output": str(receipt.get("output") or "")[:RESPONSE_TEXT_LIMIT],
    }


def _loop_receipt(transition: dict[str, Any], loop_id: int) -> dict[str, Any]:
    return {
        "loop_id": loop_id,
        "status": str(transition.get("status") or "stale"),
    }


def _handoff_receipt(services: ExecutorServices, project_key: str) -> dict[str, Any]:
    data = services.build_handoff(project_key)
    markdown = services.format_handoff(data)
    return {
        "project_key": project_key,
        "handoff_chars": len(markdown),
        "handoff_markdown": markdown[:RESPONSE_TEXT_LIMIT],
    }


def attach_execution_actions(
    result: dict[str, Any],
    *,
    cfg: Any | None = None,
    services: ExecutorServices | None = None,
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """在 proposals 回應標記可執行動作；executor 關閉時不改任何內容。"""
    cfg = cfg or get_config()
    if not executor_enabled(cfg):
        return result
    services = services or ExecutorServices()
    any_executable = False
    for item in result.get("proposals", []):
        plans = derive_actions(
            item, services=services, cfg=cfg, database=database, now=now
        )
        if not plans:
            continue
        primary = plans[0]
        item["action"] = {
            "template_id": primary.template_id,
            "risk_level": primary.risk_level,
            "label": primary.label,
        }
        item["actions"] = [
            {
                "template_id": plan.template_id,
                "risk_level": plan.risk_level,
                "label": plan.label,
                "requires_confirmation": plan.risk_level == RISK_L2,
            }
            for plan in plans
        ]
        item["risk_level"] = primary.risk_level
        item["execution_available"] = True
        any_executable = True
    result["execution_available"] = any_executable
    result["executor"] = {
        "enabled": True,
        "mode": "whitelist_templates",
        "l2_available": l2_enabled(cfg),
        "claim_boundary": EXECUTOR_CLAIM_BOUNDARY,
    }
    return result


def _find_live_proposal(
    proposal_id: str,
    *,
    database: Any | None,
    cfg: Any | None,
    now: datetime | None,
) -> dict[str, Any] | None:
    from core.proactive_secretary import build_action_proposals

    live = build_action_proposals(database=database, cfg=cfg, now=now, limit=12)
    for item in live.get("proposals", []):
        if str(item.get("proposal_id")) == str(proposal_id):
            return item
    return None


def _receipt_dict(row: AgentExecutionReceipt) -> dict[str, Any]:
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="seconds") if value else None

    return {
        "id": row.id,
        "proposal_id": row.proposal_id,
        "template_id": row.template_id,
        "risk_level": row.risk_level,
        "project_key": row.project_key,
        "action_call": row.action_call,
        "status": row.status,
        "approved_via": row.approved_via,
        "requested_at": _iso(row.requested_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "duration_seconds": row.duration_seconds,
        "output_digest": row.output_digest,
        "output_summary": row.output_summary,
        "error_code": row.error_code,
    }


def _reset_pending_confirms() -> None:
    """測試用：清空 in-memory confirm code 狀態。"""
    _PENDING_L2_CONFIRMS.clear()


def discard_pending_confirm(proposal_id: str) -> None:
    """作廢某 proposal 的待確認 confirm code（P5-R4b：Telegram 批准通道
    不支援 L2，誤觸時立即銷毀剛簽發的碼，確認流程只能回儀表板重走）。"""
    _PENDING_L2_CONFIRMS.pop(str(proposal_id), None)


def _check_l2_cooldown(
    template_id: str, *, cfg: Any, now: datetime, database: Any
) -> None:
    """每 template 冷卻：距上次實際執行（rejected 不計）未滿冷卻期即拒絕。"""
    cooldown = _l2_cooldown_seconds(cfg)
    if cooldown <= 0:
        return
    window_start = now - timedelta(seconds=cooldown)
    with database.session_scope() as session:
        recent = (
            session.query(AgentExecutionReceipt)
            .filter(
                AgentExecutionReceipt.template_id == template_id,
                AgentExecutionReceipt.status != "rejected",
                AgentExecutionReceipt.requested_at > window_start,
            )
            .order_by(AgentExecutionReceipt.requested_at.desc())
            .first()
        )
        if recent is not None:
            remaining = cooldown - (now - recent.requested_at).total_seconds()
            raise ExecutionRejected(
                "l2_cooldown_active",
                f"template {template_id} 冷卻中，約 {max(1, int(remaining))} 秒後可再執行",
                http_status=429,
            )


def _issue_confirm_code(
    proposal_id: str, plan: ActionPlan, *, cfg: Any, now: datetime
) -> dict[str, Any]:
    """產生一次性 confirm code（只回傳給呼叫端顯示，不落庫、不進 log）。"""
    ttl = _l2_confirm_ttl_seconds(cfg)
    code = f"{py_secrets.randbelow(1_000_000):06d}"
    _PENDING_L2_CONFIRMS[str(proposal_id)] = {
        "code_hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "expires_at": now + timedelta(seconds=ttl),
        "template_id": plan.template_id,
    }
    return {
        "status": "confirmation_required",
        "confirm": {
            "proposal_id": str(proposal_id),
            "template_id": plan.template_id,
            "risk_level": plan.risk_level,
            "label": plan.label,
            "confirm_code": code,
            "expires_in_seconds": ttl,
        },
        "claim_boundary": EXECUTOR_CLAIM_BOUNDARY,
    }


def _consume_confirm_code(
    proposal_id: str, template_id: str, confirm_code: str, *, now: datetime
) -> None:
    """單次有效：無論驗證成敗都先銷毀 pending 記錄（防重放與暴力嘗試）。"""
    pending = _PENDING_L2_CONFIRMS.pop(str(proposal_id), None)
    if pending is None:
        raise ExecutionRejected(
            "confirm_code_not_issued",
            "此 proposal 沒有待確認的 confirm code；請先發起執行取得確認碼",
        )
    if now > pending["expires_at"]:
        raise ExecutionRejected(
            "confirm_code_expired", "confirm code 已逾期，請重新發起執行"
        )
    if pending["template_id"] != template_id:
        raise ExecutionRejected(
            "confirm_template_mismatch", "confirm code 與目標動作不符，請重新發起執行"
        )
    provided = hashlib.sha256(str(confirm_code).encode("utf-8")).hexdigest()
    if not py_secrets.compare_digest(provided, pending["code_hash"]):
        raise ExecutionRejected(
            "confirm_code_invalid",
            "confirm code 錯誤；原確認碼已作廢，請重新發起執行",
            http_status=403,
        )


def execute_proposal(
    proposal_id: str,
    *,
    approved_via: str = "web_click",
    template_id: str | None = None,
    confirm_code: str | None = None,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    services: ExecutorServices | None = None,
    proposal_lookup: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """執行一個仍然成立的 proposal 的白名單動作；全程 fail-closed。

    ``template_id`` 只能在 server 已註冊的動作中選擇（預設 primary）；
    ``confirm_code`` 僅供 L2 二次確認。兩者都不是命令，D1 不變。
    """
    cfg = cfg or get_config()
    database = database or get_db()
    now = now or get_local_now()

    if not executor_enabled(cfg):
        raise ExecutionRejected(
            "executor_disabled",
            "executor 未啟用（proactive_secretary.executor.enabled=false）",
        )

    lookup = proposal_lookup or _find_live_proposal
    proposal = lookup(proposal_id, database=database, cfg=cfg, now=now)
    if proposal is None:
        raise ExecutionRejected(
            "proposal_not_found_or_expired",
            "proposal 不存在或其 evidence 已改變；請重新整理建議清單",
            http_status=404,
        )

    services = services or ExecutorServices()
    plans = derive_actions(
        proposal, services=services, cfg=cfg, database=database, now=now
    )
    if not plans:
        raise ExecutionRejected(
            "no_registered_action",
            "此 proposal 沒有對應的白名單動作",
        )
    if template_id is not None:
        plan = next((p for p in plans if p.template_id == template_id), None)
        if plan is None:
            raise ExecutionRejected(
                "template_not_available",
                "此 proposal 沒有這個白名單動作",
                http_status=404,
            )
    else:
        plan = plans[0]

    if plan.risk_level == RISK_L2:
        # D3：L2 需獨立開關、每 template 冷卻與一次性 confirm code，缺一即拒。
        if not l2_enabled(cfg):
            raise ExecutionRejected(
                "l2_disabled",
                "L2_MUTATE 未啟用（proactive_secretary.executor.l2.enabled=false）",
            )
        _check_l2_cooldown(plan.template_id, cfg=cfg, now=now, database=database)
        # 前置檢查在發放 confirm code 之前跑：不讓使用者走完確認流程才被拒。
        if plan.precheck is not None:
            plan.precheck()
        if not confirm_code:
            return _issue_confirm_code(proposal_id, plan, cfg=cfg, now=now)
        _consume_confirm_code(proposal_id, plan.template_id, confirm_code, now=now)
        approved_via = f"{approved_via}+confirm_code"
    elif plan.precheck is not None:
        plan.precheck()

    with database.session_scope() as session:
        active = (
            session.query(AgentExecutionReceipt)
            .filter(
                AgentExecutionReceipt.proposal_id == str(proposal_id),
                AgentExecutionReceipt.status.in_(ACTIVE_STATUSES),
            )
            .first()
        )
        if active is not None:
            raise ExecutionRejected(
                "execution_already_running",
                "此 proposal 已有進行中的執行",
            )
        row = AgentExecutionReceipt(
            proposal_id=str(proposal_id),
            template_id=plan.template_id,
            risk_level=plan.risk_level,
            project_key=proposal.get("project_key"),
            action_call=plan.call_description[:500],
            status="running",
            approved_via=approved_via[:40],
            requested_at=now,
            started_at=now,
        )
        try:
            session.add(row)
            session.flush()
        except IntegrityError as exc:
            raise ExecutionRejected(
                "execution_already_running",
                "此 proposal 已有進行中的執行",
            ) from exc
        receipt_id = row.id

    status = "failed"
    error_code: str | None = None
    result_payload: dict[str, Any] | None = None
    # subprocess template 的 timeout 由 dispatcher 內部處理（會真正 kill），
    # 外層 future 只是保險絲，多留緩衝避免搶先於內層逾時。
    outer_timeout = (
        plan.timeout_seconds + 30
        if plan.dispatch_mode == "subprocess"
        else plan.timeout_seconds
    )
    # 不用 context manager：timeout 後不得等待卡住的執行緒收尾。
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(plan.runner, {"receipt_id": receipt_id})
        try:
            result_payload = future.result(timeout=outer_timeout)
            status = "succeeded"
        except FutureTimeoutError:
            status = "timeout"
            error_code = "execution_timeout"
            future.cancel()
        except DispatchTimeout as exc:
            status = "timeout"
            error_code = "execution_timeout"
            result_payload = exc.payload
        except AgentCliFailed as exc:
            status = "failed"
            error_code = f"cli_exit_{exc.exit_code}"[:80]
            result_payload = exc.payload
        except DispatchRejected as exc:
            status = "rejected"
            error_code = exc.error_code[:80]
        except Exception as exc:  # noqa: BLE001 — 一律轉為 receipt，不外洩內部細節
            status = "failed"
            error_code = type(exc).__name__[:80]
            logger.warning(
                "Executor template %s failed: %s", plan.template_id, type(exc).__name__
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    finished = get_local_now()
    digest = None
    summary = None
    if result_payload is not None:
        canonical = json.dumps(
            result_payload, ensure_ascii=False, sort_keys=True, default=str
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        summary = json.dumps(
            {key: result_payload.get(key) for key in plan.receipt_fields},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )[:500]

    with database.session_scope() as session:
        row = session.get(AgentExecutionReceipt, receipt_id)
        if row.status == "cancelled":
            # cancel endpoint 已中止此 job（kill 了 OS 行程）；保留一級狀態，
            # 只補上輸出摘要與時間，不得覆寫回 failed/timeout。
            status = "cancelled"
            error_code = row.error_code or "cancelled_by_user"
        row.status = status
        row.finished_at = finished
        row.duration_seconds = max(0.0, (finished - now).total_seconds())
        row.output_digest = digest
        row.output_summary = summary
        row.error_code = error_code
        receipt = _receipt_dict(row)

    response: dict[str, Any] = {
        "receipt": receipt,
        "claim_boundary": EXECUTOR_CLAIM_BOUNDARY,
    }
    if status == "succeeded" and result_payload is not None:
        response["result"] = result_payload
    return response


def list_execution_receipts(
    limit: int = 20,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    database = database or get_db()
    limit = max(1, min(int(limit), 100))
    with database.session_scope() as session:
        rows = (
            session.query(AgentExecutionReceipt)
            .order_by(AgentExecutionReceipt.requested_at.desc(), AgentExecutionReceipt.id.desc())
            .limit(limit)
            .all()
        )
        receipts = [_receipt_dict(row) for row in rows]
    return {
        "receipts": receipts,
        "claim_boundary": EXECUTOR_CLAIM_BOUNDARY,
    }


def cancel_execution(
    receipt_id: int,
    *,
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """queued 直接取消；running 的 subprocess job（P5-R3）kill OS 行程後取消；
    running 的 in-process 動作無法中斷，如實拒絕（逾時由 timeout 處理）。"""
    database = database or get_db()
    now = now or get_local_now()
    receipt: dict[str, Any] | None = None
    with database.session_scope() as session:
        row = session.get(AgentExecutionReceipt, int(receipt_id))
        if row is None:
            raise ExecutionRejected("receipt_not_found", "找不到執行紀錄", http_status=404)
        if row.status == "queued":
            row.status = "cancelled"
            row.finished_at = now
            row.error_code = "cancelled_before_start"
            return {"receipt": _receipt_dict(row)}
        if row.status == "running":
            if not is_running_registered(row.id):
                raise ExecutionRejected(
                    "not_cancellable_in_process",
                    "此動作為請求內同步執行，無法中斷；逾時將由 timeout 處理",
                )
            # 先提交 cancelled、離開 session 之後才 kill：executor 執行緒是被
            # kill 喚醒的，此順序保證它收尾時必然讀到已提交的 cancelled，
            # 不會把一級狀態覆寫回 failed/succeeded。
            row.status = "cancelled"
            row.finished_at = now
            row.error_code = "cancelled_by_user"
            receipt = _receipt_dict(row)
        else:
            raise ExecutionRejected(
                "execution_already_finished",
                f"執行已結束（{row.status}），無法取消",
            )
    kill_running(int(receipt_id))
    return {"receipt": receipt}
