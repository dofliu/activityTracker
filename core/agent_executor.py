"""ADR-008 gated executor（P5-R2：L0/L1 in-process 白名單動作）。

安全契約落地：

- **D1** execute 只接受 ``proposal_id``；proposal 由 server 端即時重建
  （deterministic id），evidence 已改變的建議自動失效，永不執行過期提案。
- **D2** 動作來自程式碼註冊的白名單 template；P5-R2 全部為內部函式呼叫
  （重用 ADR-011 repo_sync、handoff_engine、open-loop lifecycle），
  **不開 subprocess、不接受任何呼叫端字串**。
- **D3** L0 唯讀可直接執行、L1 需使用者單鍵批准（HTTP 呼叫本身）＋
  execution token；L2 在 P5-R2 一律拒絕（confirm code 機制屬 P5-R3+）。
- **D4** token 驗證在 server 層（``security.execution_authorized``）。
- **D5** 每次執行寫入 ``agent_execution_receipts``（migration 014）；
  receipt 只含白名單摘要欄位與 output digest，不含內容全文或 secrets。
- **D6** 任何驗證失敗 → 拒絕；executor 總開關預設關閉，關閉時
  proposals 端點行為與 ADR-007 proposal-only 完全一致。

P5-R2 的執行為請求內同步呼叫並受硬性 timeout；逾時的執行緒無法被中斷
（in-process），receipt 如實標記 ``timeout``。cancel 只對 ``queued``
有效（同步模式不產生 queued，介面保留給 P5-R3 subprocess dispatcher）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from core.config import get_config
from core.database import get_db
from core.models import AgentExecutionReceipt
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


class ExecutionRejected(RuntimeError):
    """Fail-closed 拒絕；error_code 穩定、message 不含 secrets。"""

    def __init__(self, error_code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


@dataclass(frozen=True)
class ActionPlan:
    """derive 階段的結果：display 與 execute 共用同一份，確保一致。"""

    template_id: str
    risk_level: str
    label: str
    call_description: str
    params: dict[str, Any]
    timeout_seconds: int
    receipt_fields: tuple[str, ...]
    runner: Callable[[], dict[str, Any]]


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


def derive_action(
    proposal: dict[str, Any],
    *,
    services: ExecutorServices,
) -> ActionPlan | None:
    """每個 proposal 對應至多一個 deterministic template；display 與 execute 共用。"""
    proposal_type = str(proposal.get("proposal_type") or "")
    project_key = str(proposal.get("project_key") or "")

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
                runner=lambda: _safe_repo_receipt(
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
                runner=lambda: _loop_receipt(
                    services.loop_transition(loop_id, "stale", "via secretary executor"),
                    loop_id,
                ),
            )

    if project_key and proposal_type != "verify_extension_heartbeat":
        return ActionPlan(
            template_id="generate_handoff",
            risk_level=RISK_L0,
            label=f"產生 {project_key} 的 Context Handoff（唯讀）",
            call_description=f"handoff_engine.build_project_handoff({project_key!r})",
            params={"project_key": project_key},
            timeout_seconds=60,
            receipt_fields=("project_key", "handoff_chars"),
            runner=lambda: _handoff_receipt(services, project_key),
        )

    return None


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
) -> dict[str, Any]:
    """在 proposals 回應標記可執行動作；executor 關閉時不改任何內容。"""
    cfg = cfg or get_config()
    if not executor_enabled(cfg):
        return result
    services = services or ExecutorServices()
    any_executable = False
    for item in result.get("proposals", []):
        plan = derive_action(item, services=services)
        if plan is None:
            continue
        item["action"] = {
            "template_id": plan.template_id,
            "risk_level": plan.risk_level,
            "label": plan.label,
        }
        item["risk_level"] = plan.risk_level
        item["execution_available"] = True
        any_executable = True
    result["execution_available"] = any_executable
    result["executor"] = {
        "enabled": True,
        "mode": "whitelist_templates_in_process",
        "l2_available": False,
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


def execute_proposal(
    proposal_id: str,
    *,
    approved_via: str = "web_click",
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    services: ExecutorServices | None = None,
    proposal_lookup: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """執行一個仍然成立的 proposal 的白名單動作；全程 fail-closed。"""
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
    plan = derive_action(proposal, services=services)
    if plan is None:
        raise ExecutionRejected(
            "no_registered_action",
            "此 proposal 沒有對應的白名單動作",
        )
    if plan.risk_level == RISK_L2:
        raise ExecutionRejected(
            "l2_confirmation_not_available",
            "L2_MUTATE 需要二次確認機制（P5-R3+），目前不可執行",
        )

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
    # 不用 context manager：timeout 後不得等待卡住的執行緒收尾。
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(plan.runner)
        try:
            result_payload = future.result(timeout=plan.timeout_seconds)
            status = "succeeded"
        except FutureTimeoutError:
            status = "timeout"
            error_code = "execution_timeout"
            future.cancel()
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
    """只有 queued 可取消；P5-R2 同步 in-process 執行無法中斷，如實拒絕。"""
    database = database or get_db()
    now = now or get_local_now()
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
            raise ExecutionRejected(
                "not_cancellable_in_process",
                "P5-R2 的動作為請求內同步執行，無法中斷；逾時將由 timeout 處理",
            )
        raise ExecutionRejected(
            "execution_already_finished",
            f"執行已結束（{row.status}），無法取消",
        )
