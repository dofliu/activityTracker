"""P5-R5 使用者自訂排程任務（ADR-008 階段 5）。

安全契約（疊加在 ADR-008 D1–D6 之上）：

- **只能排程已註冊的 schedulable template**，且註冊表僅接受
  ``L0_READ_ONLY``（模組載入即檢查）；L1/L2 需要人在場批准，永遠不可
  排程自動執行（D3）。呼叫端一樣無法提供 command / path / argv——
  參數需通過各 template 的白名單驗證，未知欄位一律拒絕。
- **開關疊加**：``executor.enabled`` 且 ``executor.scheduled_tasks.enabled``
  （皆預設關閉）才會排程執行；關閉時 mutation API 回 409、排程 tick
  直接跳過。
- **每次執行寫 audit receipt**（``agent_execution_receipts``，
  ``approved_via=schedule``，``proposal_id=scheduled_task:<id>``）；
  migration 014 的 active-proposal 唯一索引天然防止同一任務重疊執行。
- **補跑不重複**：due 判定以「最近一次應執行時刻」對「上次執行（或
  建立）時間」比較——服務重啟或停機錯過的排程，恢復後只補跑一次。
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy.exc import IntegrityError

from core.agent_executor import (
    EXECUTOR_CLAIM_BOUNDARY,
    ExecutionRejected,
    RISK_L0,
    executor_enabled,
)
from core.config import get_config
from core.database import get_db
from core.models import AgentExecutionReceipt, ProjectState, SecretaryScheduledTask
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.ScheduledTasks")

SCHEDULE_KINDS = ("daily", "weekly", "monthly")
DEFAULT_MAX_TASKS = 20

SCHEDULED_TASKS_CLAIM_BOUNDARY = (
    "排程只會自動執行 server 註冊的 L0 唯讀 template 並寫 audit receipt；"
    "L1/L2 動作永遠需要人在場批准，不可排程。" + EXECUTOR_CLAIM_BOUNDARY
)


class ScheduleRejected(ExecutionRejected):
    """排程任務的 fail-closed 拒絕；沿用 ExecutionRejected 介面。"""


@dataclass(frozen=True)
class SchedulableTemplate:
    """可被排程的白名單動作；只有 L0_READ_ONLY 允許註冊。"""

    template_id: str
    risk_level: str
    label: str
    description: str
    params_schema: dict[str, str]  # 允許的參數名 -> 說明（白名單）
    validate_params: Callable[[dict[str, Any], Any], dict[str, Any]]  # (params, database)
    build_runner: Callable[[dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]]]
    receipt_fields: tuple[str, ...]
    timeout_seconds: int


def scheduled_tasks_enabled(cfg: Any | None = None) -> bool:
    """疊加開關：executor 總開關 + 排程任務獨立開關，皆預設關閉。"""
    cfg = cfg or get_config()
    return executor_enabled(cfg) and bool(
        cfg.get("proactive_secretary.executor.scheduled_tasks.enabled", False)
    )


def _max_tasks(cfg: Any) -> int:
    try:
        raw = int(cfg.get("proactive_secretary.executor.scheduled_tasks.max_tasks", DEFAULT_MAX_TASKS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TASKS
    return min(100, max(1, raw))


# ---- schedulable template registry（全部 L0、內部函式呼叫） ----


def _reject_unknown_params(params: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(params) - allowed
    if unknown:
        raise ScheduleRejected(
            "unknown_param",
            f"不接受的參數：{', '.join(sorted(unknown))}",
            http_status=422,
        )


def _validate_no_params(params: dict[str, Any], _database: Any) -> dict[str, Any]:
    _reject_unknown_params(params, set())
    return {}


def _validate_handoff_params(params: dict[str, Any], database: Any) -> dict[str, Any]:
    _reject_unknown_params(params, {"project_key"})
    project_key = str(params.get("project_key") or "").strip()
    if not project_key:
        raise ScheduleRejected("missing_project_key", "需要 project_key", http_status=422)
    with database.session_scope() as session:
        exists = (
            session.query(ProjectState.id)
            .filter(
                (ProjectState.project_key == project_key)
                | (ProjectState.display_name == project_key)
            )
            .first()
        )
    if exists is None:
        raise ScheduleRejected(
            "project_not_found",
            "project_key 不存在於 project_states",
            http_status=404,
        )
    return {"project_key": project_key}


def _run_handoff(params: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    project_key = params["project_key"]

    def _runner(ctx: dict[str, Any]) -> dict[str, Any]:
        from core.handoff_engine import build_project_handoff, format_handoff_markdown

        markdown = format_handoff_markdown(build_project_handoff(project_key))
        cfg = get_config()
        out_dir = resolve_runtime_path(cfg.get("exporters.reports_dir", "reports")) / "handoffs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = get_local_now().strftime("%Y%m%d")
        safe_key = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in project_key)[:60]
        output_path = out_dir / f"Handoff_{safe_key}_{stamp}.md"
        output_path.write_text(markdown, encoding="utf-8")
        return {
            "project_key": project_key,
            "handoff_chars": len(markdown),
            "output_path": str(output_path),
        }

    return _runner


def _run_rollup(kind: str) -> Callable[[dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]]]:
    def _build(_params: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def _runner(ctx: dict[str, Any]) -> dict[str, Any]:
            from synthesizer.rollup import build_report_rollup

            return build_report_rollup(kind)

        return _runner

    return _build


def _run_status_draft(_params: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _runner(ctx: dict[str, Any]) -> dict[str, Any]:
        from core.status_draft import build_status_draft

        return build_status_draft()

    return _runner


def _validate_active_handoff_params(params: dict[str, Any], _database: Any) -> dict[str, Any]:
    _reject_unknown_params(params, {"hours", "max_projects"})
    try:
        hours = int(params.get("hours", 24))
        max_projects = int(params.get("max_projects", 10))
    except (TypeError, ValueError) as exc:
        raise ScheduleRejected("invalid_params", "hours／max_projects 需為整數", http_status=422) from exc
    if not (1 <= hours <= 24 * 7) or not (1 <= max_projects <= 30):
        raise ScheduleRejected("invalid_params", "hours 需在 1–168、max_projects 需在 1–30", http_status=422)
    return {"hours": hours, "max_projects": max_projects}


def _run_active_handoffs(params: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _runner(ctx: dict[str, Any]) -> dict[str, Any]:
        from core.secretary_packs import build_active_handoffs

        return build_active_handoffs(
            hours=int(params.get("hours", 24)), max_projects=int(params.get("max_projects", 10))
        )

    return _runner


def _run_morning_pack(_params: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _runner(ctx: dict[str, Any]) -> dict[str, Any]:
        from core.secretary_packs import build_morning_pack

        return build_morning_pack()

    return _runner


def _run_repo_sync_report(_params: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _runner(ctx: dict[str, Any]) -> dict[str, Any]:
        from core.repo_sync_report import build_repo_sync_report

        return build_repo_sync_report()

    return _runner


SCHEDULABLE_TEMPLATES: dict[str, SchedulableTemplate] = {
    template.template_id: template
    for template in (
        SchedulableTemplate(
            template_id="generate_handoff",
            risk_level=RISK_L0,
            label="產生指定專案的 Context Handoff（唯讀，存入 reports/handoffs）",
            description="重用 handoff_engine；輸出 markdown 檔，不改任何資料。",
            params_schema={"project_key": "project_states 內既有的專案"},
            validate_params=_validate_handoff_params,
            build_runner=_run_handoff,
            receipt_fields=("project_key", "handoff_chars", "output_path"),
            timeout_seconds=60,
        ),
        SchedulableTemplate(
            template_id="weekly_report_rollup",
            risk_level=RISK_L0,
            label="週報 rollup：彙整上一個完整週的每日摘要",
            description="讀 daily_summaries reduce 成週報；LLM 失敗回退 deterministic。",
            params_schema={},
            validate_params=_validate_no_params,
            build_runner=_run_rollup("weekly"),
            receipt_fields=(
                "kind", "period_label", "days_with_summary", "days_missing",
                "llm_used", "output_path",
            ),
            timeout_seconds=600,
        ),
        SchedulableTemplate(
            template_id="monthly_report_rollup",
            risk_level=RISK_L0,
            label="月報 rollup：彙整上一個完整月份的每日摘要",
            description="讀 daily_summaries reduce 成月報；LLM 失敗回退 deterministic。",
            params_schema={},
            validate_params=_validate_no_params,
            build_runner=_run_rollup("monthly"),
            receipt_fields=(
                "kind", "period_label", "days_with_summary", "days_missing",
                "llm_used", "output_path",
            ),
            timeout_seconds=600,
        ),
        SchedulableTemplate(
            template_id="morning_pack",
            risk_level=RISK_L0,
            label="早晨包：Repo 同步報告＋STATUS 過期草稿＋活躍專案 Handoff（全部唯讀）",
            description="把三個既有 L0 動作綁成一次排程；晨報與「今日行動」會引用它的收據。不 fetch、不改 repo。",
            params_schema={},
            validate_params=_validate_no_params,
            build_runner=_run_morning_pack,
            receipt_fields=(
                "repos_scanned", "needs_pull", "needs_push", "diverged",
                "stale_status", "handoffs_written", "errors",
            ),
            timeout_seconds=600,
        ),
        SchedulableTemplate(
            template_id="handoff_active_projects",
            risk_level=RISK_L0,
            label="活躍專案 Handoff：為最近 N 小時內有活動的專案各產一份 Context Handoff",
            description="重用 handoff_engine，寫入 reports/handoffs；「上次做到哪」永遠有現成接續 prompt。",
            params_schema={"hours": "回看幾小時內有活動（1–168，預設 24）", "max_projects": "最多幾個專案（1–30，預設 10）"},
            validate_params=_validate_active_handoff_params,
            build_runner=_run_active_handoffs,
            receipt_fields=("hours", "projects_considered", "handoffs_written", "errors", "output_dir"),
            timeout_seconds=300,
        ),
        SchedulableTemplate(
            template_id="repo_sync_report",
            risk_level=RISK_L0,
            label="Repo 同步報告：掃描全部本機 repo 的 cached 同步狀態，產生報告與提案快照",
            description="只讀 git status 與本機 remote-tracking ref，不連網、不改 worktree；快照讓小秘書提出需要 pull／push 的 repo（執行仍需批准）。",
            params_schema={},
            validate_params=_validate_no_params,
            build_runner=_run_repo_sync_report,
            receipt_fields=(
                "repos_scanned", "needs_pull", "needs_push", "diverged", "dirty",
                "no_upstream", "never_fetched", "output_path",
            ),
            timeout_seconds=300,
        ),
        SchedulableTemplate(
            template_id="status_snapshot_draft",
            risk_level=RISK_L0,
            label="STATUS 維護草稿：點名 last_updated 已落後觀測活動的 repo",
            description="只讀各 repo 的 STATUS.yaml；草稿寫入 reports/status_drafts，絕不改 repo。",
            params_schema={},
            validate_params=_validate_no_params,
            build_runner=_run_status_draft,
            receipt_fields=(
                "repos_scanned", "repos_with_status", "stale_count",
                "parse_errors", "output_path",
            ),
            timeout_seconds=120,
        ),
    )
}

# 模組載入即強制：排程註冊表只接受 L0 唯讀動作（contract test 亦驗證）。
for _template in SCHEDULABLE_TEMPLATES.values():
    if _template.risk_level != RISK_L0:
        raise RuntimeError(
            f"Schedulable template {_template.template_id} must be L0_READ_ONLY"
        )


# ---- schedule 驗證與 due 計算 ----


def _parse_run_time(value: Any) -> tuple[int, int, str]:
    try:
        hour_str, minute_str = str(value).strip().split(":", 1)
        hour, minute = int(hour_str), int(minute_str)
    except (TypeError, ValueError) as exc:
        raise ScheduleRejected("invalid_run_time", "run_time 需為 HH:MM", http_status=422) from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleRejected("invalid_run_time", "run_time 需為 HH:MM", http_status=422)
    return hour, minute, f"{hour:02d}:{minute:02d}"


def _validate_schedule(
    schedule_kind: str,
    run_time: Any,
    weekday: Any,
    day_of_month: Any,
) -> dict[str, Any]:
    kind = str(schedule_kind or "").strip().lower()
    if kind not in SCHEDULE_KINDS:
        raise ScheduleRejected(
            "invalid_schedule_kind",
            f"schedule_kind 只接受 {', '.join(SCHEDULE_KINDS)}",
            http_status=422,
        )
    _, _, normalized_time = _parse_run_time(run_time)
    normalized: dict[str, Any] = {
        "schedule_kind": kind,
        "run_time": normalized_time,
        "weekday": None,
        "day_of_month": None,
    }
    if kind == "weekly":
        try:
            weekday_int = int(weekday)
        except (TypeError, ValueError):
            weekday_int = -1
        if not 0 <= weekday_int <= 6:
            raise ScheduleRejected(
                "invalid_weekday", "weekly 需要 weekday 0（週一）–6（週日）", http_status=422
            )
        normalized["weekday"] = weekday_int
    if kind == "monthly":
        try:
            day_int = int(day_of_month)
        except (TypeError, ValueError):
            day_int = -1
        if not 1 <= day_int <= 28:
            raise ScheduleRejected(
                "invalid_day_of_month",
                "monthly 需要 day_of_month 1–28（避開月長歧義）",
                http_status=422,
            )
        normalized["day_of_month"] = day_int
    return normalized


def latest_occurrence(
    schedule_kind: str,
    run_time: str,
    weekday: Optional[int],
    day_of_month: Optional[int],
    now: datetime,
) -> datetime:
    """最近一次「應執行時刻」（<= now）。"""
    hour, minute, _ = _parse_run_time(run_time)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule_kind == "daily":
        if candidate > now:
            candidate -= timedelta(days=1)
        return candidate
    if schedule_kind == "weekly":
        offset = (now.weekday() - int(weekday or 0)) % 7
        candidate -= timedelta(days=offset)
        if candidate > now:
            candidate -= timedelta(days=7)
        return candidate
    if schedule_kind == "monthly":
        day = int(day_of_month or 1)
        candidate = candidate.replace(day=day)
        if candidate > now:
            first_of_month = candidate.replace(day=1)
            last_month_end = first_of_month - timedelta(days=1)
            candidate = candidate.replace(year=last_month_end.year, month=last_month_end.month)
        return candidate
    raise ScheduleRejected("invalid_schedule_kind", f"未知 schedule_kind：{schedule_kind}")


def _is_due(task: SecretaryScheduledTask, now: datetime) -> bool:
    if not task.enabled:
        return False
    occurrence = latest_occurrence(
        task.schedule_kind, task.run_time, task.weekday, task.day_of_month, now
    )
    anchor = task.last_run_at or task.created_at
    return anchor is None or anchor < occurrence


# ---- CRUD ----


def _task_dict(row: SecretaryScheduledTask, now: datetime | None = None) -> dict[str, Any]:
    template = SCHEDULABLE_TEMPLATES.get(row.template_id)
    try:
        params = json.loads(row.params_json) if row.params_json else {}
    except ValueError:
        params = {}

    def _iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="seconds") if value else None

    return {
        "id": row.id,
        "template_id": row.template_id,
        "template_label": template.label if template else None,
        "template_registered": template is not None,
        "params": params,
        "schedule_kind": row.schedule_kind,
        "run_time": row.run_time,
        "weekday": row.weekday,
        "day_of_month": row.day_of_month,
        "enabled": bool(row.enabled),
        "last_run_at": _iso(row.last_run_at),
        "last_status": row.last_status,
        "last_receipt_id": row.last_receipt_id,
        "last_error_code": row.last_error_code,
        "created_at": _iso(row.created_at),
    }


def list_scheduled_tasks(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    database = database or get_db()
    cfg = cfg or get_config()
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryScheduledTask)
            .order_by(SecretaryScheduledTask.id.asc())
            .all()
        )
        tasks = [_task_dict(row) for row in rows]
    return {
        "enabled": scheduled_tasks_enabled(cfg),
        "templates": [
            {
                "template_id": template.template_id,
                "risk_level": template.risk_level,
                "label": template.label,
                "description": template.description,
                "params_schema": template.params_schema,
            }
            for template in SCHEDULABLE_TEMPLATES.values()
        ],
        "tasks": tasks,
        "claim_boundary": SCHEDULED_TASKS_CLAIM_BOUNDARY,
    }


def _require_enabled(cfg: Any) -> None:
    if not scheduled_tasks_enabled(cfg):
        raise ScheduleRejected(
            "scheduled_tasks_disabled",
            "排程任務未啟用（proactive_secretary.executor.scheduled_tasks.enabled=false）",
        )


def create_scheduled_task(
    payload: dict[str, Any],
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    _require_enabled(cfg)

    template_id = str(payload.get("template_id") or "").strip()
    template = SCHEDULABLE_TEMPLATES.get(template_id)
    if template is None:
        # 已註冊的 executor template（L1/L2）與未知 ID 一視同仁拒絕。
        raise ScheduleRejected(
            "template_not_schedulable",
            "此 template 不在可排程白名單（只有 L0 唯讀動作可排程）",
            http_status=422,
        )
    raw_params = payload.get("params") or {}
    if not isinstance(raw_params, dict):
        raise ScheduleRejected("invalid_params", "params 需為物件", http_status=422)
    params = template.validate_params(raw_params, database)
    schedule = _validate_schedule(
        payload.get("schedule_kind"),
        payload.get("run_time", "08:30"),
        payload.get("weekday"),
        payload.get("day_of_month"),
    )

    with database.session_scope() as session:
        count = session.query(SecretaryScheduledTask).count()
        if count >= _max_tasks(cfg):
            raise ScheduleRejected(
                "too_many_scheduled_tasks",
                f"排程任務數已達上限（{_max_tasks(cfg)}）",
            )
        row = SecretaryScheduledTask(
            template_id=template_id,
            params_json=json.dumps(params, ensure_ascii=False, sort_keys=True),
            schedule_kind=schedule["schedule_kind"],
            run_time=schedule["run_time"],
            weekday=schedule["weekday"],
            day_of_month=schedule["day_of_month"],
            enabled=bool(payload.get("enabled", True)),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        task = _task_dict(row)
    return {"task": task, "claim_boundary": SCHEDULED_TASKS_CLAIM_BOUNDARY}


def update_scheduled_task(
    task_id: int,
    payload: dict[str, Any],
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    _require_enabled(cfg)

    with database.session_scope() as session:
        row = session.get(SecretaryScheduledTask, int(task_id))
        if row is None:
            raise ScheduleRejected("task_not_found", "找不到排程任務", http_status=404)
        template = SCHEDULABLE_TEMPLATES.get(row.template_id)
        if "params" in payload and payload["params"] is not None:
            if template is None:
                raise ScheduleRejected(
                    "template_not_schedulable",
                    "此任務的 template 已不在白名單，無法更新參數",
                    http_status=409,
                )
            if not isinstance(payload["params"], dict):
                raise ScheduleRejected("invalid_params", "params 需為物件", http_status=422)
            params = template.validate_params(payload["params"], database)
            row.params_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
        schedule_fields = ("schedule_kind", "run_time", "weekday", "day_of_month")
        if any(payload.get(field) is not None for field in schedule_fields):
            schedule = _validate_schedule(
                payload.get("schedule_kind", row.schedule_kind),
                payload.get("run_time", row.run_time),
                payload.get("weekday", row.weekday),
                payload.get("day_of_month", row.day_of_month),
            )
            row.schedule_kind = schedule["schedule_kind"]
            row.run_time = schedule["run_time"]
            row.weekday = schedule["weekday"]
            row.day_of_month = schedule["day_of_month"]
        if payload.get("enabled") is not None:
            row.enabled = bool(payload["enabled"])
        row.updated_at = now
        session.flush()
        task = _task_dict(row)
    return {"task": task, "claim_boundary": SCHEDULED_TASKS_CLAIM_BOUNDARY}


def delete_scheduled_task(
    task_id: int,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    database = database or get_db()
    cfg = cfg or get_config()
    _require_enabled(cfg)
    with database.session_scope() as session:
        row = session.get(SecretaryScheduledTask, int(task_id))
        if row is None:
            raise ScheduleRejected("task_not_found", "找不到排程任務", http_status=404)
        session.delete(row)
    return {"deleted": int(task_id), "claim_boundary": SCHEDULED_TASKS_CLAIM_BOUNDARY}


# ---- 執行（audit receipt 生命週期與 agent_executor L0 路徑一致） ----


def _execute_task_row(
    row_id: int,
    template: SchedulableTemplate,
    params: dict[str, Any],
    *,
    database: Any,
    now: datetime,
    approved_via: str,
) -> dict[str, Any]:
    proposal_id = f"scheduled_task:{row_id}"
    with database.session_scope() as session:
        receipt_row = AgentExecutionReceipt(
            proposal_id=proposal_id,
            template_id=template.template_id,
            risk_level=template.risk_level,
            project_key=params.get("project_key"),
            action_call=f"scheduled_tasks.run({template.template_id}, task_id={row_id})"[:500],
            status="running",
            approved_via=approved_via[:40],
            requested_at=now,
            started_at=now,
        )
        try:
            session.add(receipt_row)
            session.flush()
        except IntegrityError as exc:
            raise ScheduleRejected(
                "execution_already_running", "此排程任務已有進行中的執行"
            ) from exc
        receipt_id = receipt_row.id

    status = "failed"
    error_code: str | None = None
    result_payload: dict[str, Any] | None = None
    runner = template.build_runner(params)
    # 與 agent_executor L0 相同：timeout 後不等待卡住的執行緒收尾。
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(runner, {"receipt_id": receipt_id})
        try:
            result_payload = future.result(timeout=template.timeout_seconds)
            status = "succeeded"
        except FutureTimeoutError:
            status = "timeout"
            error_code = "execution_timeout"
            future.cancel()
        except Exception as exc:  # noqa: BLE001 — 一律轉為 receipt，不外洩內部細節
            status = "failed"
            error_code = type(exc).__name__[:80]
            logger.warning(
                "Scheduled template %s failed: %s", template.template_id, type(exc).__name__
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    finished = get_local_now()
    digest = None
    summary = None
    if result_payload is not None:
        canonical = json.dumps(result_payload, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        summary = json.dumps(
            {key: result_payload.get(key) for key in template.receipt_fields},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )[:500]

    with database.session_scope() as session:
        receipt_row = session.get(AgentExecutionReceipt, receipt_id)
        receipt_row.status = status
        receipt_row.finished_at = finished
        receipt_row.duration_seconds = max(0.0, (finished - now).total_seconds())
        receipt_row.output_digest = digest
        receipt_row.output_summary = summary
        receipt_row.error_code = error_code

        task_row = session.get(SecretaryScheduledTask, row_id)
        if task_row is not None:
            # 失敗也前移 last_run_at：錯過或失敗的排程等下一個週期，不重試轟炸。
            task_row.last_run_at = now
            task_row.last_status = status
            task_row.last_receipt_id = receipt_id
            task_row.last_error_code = error_code
            task_row.updated_at = finished

    response: dict[str, Any] = {
        "receipt_id": receipt_id,
        "task_id": row_id,
        "template_id": template.template_id,
        "status": status,
        "error_code": error_code,
        "claim_boundary": SCHEDULED_TASKS_CLAIM_BOUNDARY,
    }
    if status == "succeeded" and result_payload is not None:
        response["result"] = result_payload
    return response


def run_scheduled_task_now(
    task_id: int,
    *,
    approved_via: str = "web_click",
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """立即執行一次（仍需啟用 + execution token 由 API 層把關）。"""
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    _require_enabled(cfg)
    with database.session_scope() as session:
        row = session.get(SecretaryScheduledTask, int(task_id))
        if row is None:
            raise ScheduleRejected("task_not_found", "找不到排程任務", http_status=404)
        template = SCHEDULABLE_TEMPLATES.get(row.template_id)
        if template is None:
            raise ScheduleRejected(
                "template_not_schedulable",
                "此任務的 template 已不在白名單",
                http_status=409,
            )
        try:
            params = json.loads(row.params_json) if row.params_json else {}
        except ValueError:
            params = {}
        row_id = row.id
    return _execute_task_row(
        row_id, template, params, database=database, now=now, approved_via=approved_via
    )


def run_due_scheduled_tasks(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """排程 tick：執行所有到期任務（每個錯過的排程最多補跑一次）。"""
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    if not scheduled_tasks_enabled(cfg):
        return {"status": "disabled", "ran": []}

    with database.session_scope() as session:
        rows = (
            session.query(SecretaryScheduledTask)
            .filter(SecretaryScheduledTask.enabled.is_(True))
            .order_by(SecretaryScheduledTask.id.asc())
            .all()
        )
        due = [
            (row.id, row.template_id, row.params_json)
            for row in rows
            if _is_due(row, now)
        ]

    ran: list[dict[str, Any]] = []
    for row_id, template_id, params_json in due:
        template = SCHEDULABLE_TEMPLATES.get(template_id)
        if template is None:
            logger.warning("Scheduled task %s references unknown template %s", row_id, template_id)
            with database.session_scope() as session:
                task_row = session.get(SecretaryScheduledTask, row_id)
                if task_row is not None:
                    task_row.last_run_at = now
                    task_row.last_status = "rejected"
                    task_row.last_error_code = "template_not_schedulable"
                    task_row.updated_at = now
            continue
        try:
            params = json.loads(params_json) if params_json else {}
        except ValueError:
            params = {}
        try:
            ran.append(
                _execute_task_row(
                    row_id,
                    template,
                    params,
                    database=database,
                    now=now,
                    approved_via="schedule",
                )
            )
        except ScheduleRejected as exc:
            logger.info("Scheduled task %s skipped: %s", row_id, exc.error_code)
        except Exception as exc:  # noqa: BLE001 — 單一任務失敗不阻斷其餘排程
            logger.error("Scheduled task %s crashed: %s", row_id, type(exc).__name__)
    return {"status": "ok", "ran": ran}
