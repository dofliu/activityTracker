"""小秘書「每日包」：把幾個既有的 L0 唯讀動作綁成一次排程，讓秘書更主動。

- **早晨包（morning_pack）**：Repo 同步報告（cached、不連網）＋ STATUS 過期點名草稿
  ＋ 活躍專案 Handoff。全部是既有的唯讀 runner，只是一次排程跑完，並留下一份
  精簡收據讓晨報與儀表板「今日行動」可以引用。
- **活躍專案 Handoff（handoff_active_projects）**：對最近 N 小時內有活動的專案各產一份
  Context Handoff（`reports/handoffs/`），「上次做到哪」永遠有現成的接續 prompt。
- **預設排程（presets）**：一鍵建立「早晨包 07:30、晚間 Handoff 21:30」兩個每日任務；
  已存在就不重複建立。仍受 ADR-008 的疊加開關（executor + scheduled_tasks）約束。

契約：這裡沒有任何 L1/L2 動作、不 fetch、不改任何 repo；失敗的子步驟如實記在
``errors``，不讓整包因單一步驟失敗而消失。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.config import get_config
from core.database import get_db
from core.models import AgentExecutionReceipt
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.SecretaryPacks")

MORNING_PACK_TEMPLATE = "morning_pack"
ACTIVE_HANDOFFS_TEMPLATE = "handoff_active_projects"

DEFAULT_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "template_id": MORNING_PACK_TEMPLATE,
        "params": {},
        "schedule_kind": "daily",
        "run_time": "07:30",
        "label": "早晨包（同步報告＋STATUS 草稿＋活躍專案 Handoff）",
    },
    {
        "template_id": ACTIVE_HANDOFFS_TEMPLATE,
        "params": {"hours": 24, "max_projects": 10},
        "schedule_kind": "daily",
        "run_time": "21:30",
        "label": "晚間：為今天有活動的專案產生 Handoff",
    },
)

PACK_CLAIM_BOUNDARY = (
    "早晨包只執行 L0 唯讀動作（cached 同步報告、STATUS 草稿、Handoff 檔），"
    "不 fetch、不改任何 repo；計數反映執行當下的本機認知。"
)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)[:60]


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def build_active_handoffs(
    *,
    hours: int = 24,
    max_projects: int = 10,
    cfg: Any | None = None,
    now: datetime | None = None,
    projects: list[dict[str, Any]] | None = None,
    build: Callable[[str], dict[str, Any]] | None = None,
    fmt: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """為最近 ``hours`` 小時內有活動的專案各寫一份 Handoff markdown（唯讀）。"""
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    hours = max(1, min(int(hours), 24 * 7))
    max_projects = max(1, min(int(max_projects), 30))

    if projects is None:
        from core.project_engine import get_active_projects_list

        projects = get_active_projects_list()
    if build is None or fmt is None:
        from core.handoff_engine import build_project_handoff, format_handoff_markdown

        build = build or build_project_handoff
        fmt = fmt or format_handoff_markdown

    cutoff = now - timedelta(hours=hours)
    candidates: list[dict[str, Any]] = []
    for project in projects:
        raw = str(project.get("last_activity_at") or "")
        try:
            last = datetime.fromisoformat(raw.replace(" ", "T")[:19])
        except ValueError:
            continue
        if last >= cutoff:
            candidates.append(project)
    candidates.sort(key=lambda item: str(item.get("last_activity_at") or ""), reverse=True)
    selected = candidates[:max_projects]

    out_dir = resolve_runtime_path(cfg.get("exporters.reports_dir", "reports")) / "handoffs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d")
    written: list[str] = []
    errors: list[str] = []
    for project in selected:
        key = str(project.get("project_key") or "")
        try:
            markdown = fmt(build(key))
            path = out_dir / f"Handoff_{_safe_name(key)}_{stamp}.md"
            path.write_text(markdown, encoding="utf-8")
            written.append(key)
        except Exception as exc:  # noqa: BLE001 — 單一專案失敗不該讓其他專案沒有 Handoff
            errors.append(f"{key}: {type(exc).__name__}")
    return {
        "hours": hours,
        "projects_considered": len(candidates),
        "handoffs_written": len(written),
        "projects": written,
        "errors": errors,
        "output_dir": str(out_dir),
    }


def build_morning_pack(
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    repo_sync: Callable[[], dict[str, Any]] | None = None,
    status_draft: Callable[[], dict[str, Any]] | None = None,
    handoffs: Callable[[], dict[str, Any]] | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    """三個 L0 步驟各自 try/except；收據保持扁平、短小（output_summary 只有 500 字）。"""
    cfg = cfg or get_config()
    now = now or get_local_now()
    errors: list[str] = []
    receipt: dict[str, Any] = {
        "repos_scanned": None, "needs_pull": None, "needs_push": None, "diverged": None,
        "stale_status": None, "handoffs_written": None,
    }

    def _step(name: str, fn: Callable[[], dict[str, Any]] | None, default: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        try:
            return (fn or default)()
        except Exception as exc:  # noqa: BLE001 — 如實記錯，其他步驤照跑
            logger.warning("morning pack step %s failed: %s", name, exc)
            errors.append(f"{name}: {type(exc).__name__}")
            return None

    def _default_repo_sync() -> dict[str, Any]:
        from core.repo_sync_report import build_repo_sync_report

        return build_repo_sync_report(cfg=cfg, now=now)

    def _default_status_draft() -> dict[str, Any]:
        from core.status_draft import build_status_draft

        return build_status_draft()

    def _default_handoffs() -> dict[str, Any]:
        return build_active_handoffs(hours=24, max_projects=10, cfg=cfg, now=now)

    sync = _step("repo_sync_report", repo_sync, _default_repo_sync)
    if sync:
        receipt.update({
            "repos_scanned": sync.get("repos_scanned"),
            "needs_pull": sync.get("needs_pull"),
            "needs_push": sync.get("needs_push"),
            "diverged": sync.get("diverged"),
        })
    draft = _step("status_snapshot_draft", status_draft, _default_status_draft)
    if draft:
        receipt["stale_status"] = draft.get("stale_count")
    hand = _step("handoff_active_projects", handoffs, _default_handoffs)
    if hand:
        receipt["handoffs_written"] = hand.get("handoffs_written")
    receipt["errors"] = errors
    receipt["generated_at"] = now.isoformat(timespec="seconds")
    # 秘書自己的觀察（ADR-012）：只寫當日一次、標記 observation、介面可一鍵刪除。
    try:
        from core.secretary_memory import observations_from_pack

        receipt["observations_written"] = len(
            observations_from_pack(receipt, database=database, now=now, cfg=cfg)
        )
    except Exception as exc:  # noqa: BLE001 — 記憶區故障不得讓早晨包失敗
        logger.warning("morning pack observations skipped: %s", exc)
        receipt["observations_written"] = 0
    receipt["claim_boundary"] = PACK_CLAIM_BOUNDARY
    return receipt


def latest_pack_summary(
    *,
    database: Any | None = None,
    now: datetime | None = None,
    max_age_hours: int = 36,
) -> dict[str, Any] | None:
    """最近一次成功的早晨包收據（只讀 audit receipt，不重跑）；過期回 None。"""
    database = database or get_db()
    now = _naive(now or get_local_now())
    with database.session_scope() as session:
        row = (
            session.query(AgentExecutionReceipt)
            .filter(
                AgentExecutionReceipt.template_id == MORNING_PACK_TEMPLATE,
                AgentExecutionReceipt.status == "succeeded",
            )
            .order_by(AgentExecutionReceipt.finished_at.desc(), AgentExecutionReceipt.id.desc())
            .first()
        )
        if row is None or not row.output_summary:
            return None
        finished = _naive(row.finished_at or row.requested_at)
        try:
            summary = json.loads(row.output_summary)
        except ValueError:
            return None
        approved_via = row.approved_via
    if finished is None or now - finished > timedelta(hours=max_age_hours):
        return None
    summary = dict(summary) if isinstance(summary, dict) else {}
    summary["finished_at"] = finished.isoformat(timespec="seconds")
    summary["approved_via"] = approved_via
    return summary


def pack_summary_line(summary: dict[str, Any] | None) -> str | None:
    """晨報／今日行動用的一行摘要；沒有收據就不說話。"""
    if not summary:
        return None
    parts: list[str] = []
    if summary.get("needs_pull") is not None or summary.get("needs_push") is not None:
        parts.append(f"repo 需 pull {summary.get('needs_pull') or 0}、需 push {summary.get('needs_push') or 0}")
    if summary.get("stale_status") is not None:
        parts.append(f"STATUS 過期 {summary.get('stale_status') or 0}")
    if summary.get("handoffs_written") is not None:
        parts.append(f"Handoff {summary.get('handoffs_written') or 0} 份")
    errors = summary.get("errors") or []
    if errors:
        parts.append(f"{len(errors)} 步失敗")
    return "早晨包：" + "、".join(parts) if parts else None


def ensure_default_schedules(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """建立缺少的預設排程；已有同 template 的任務就跳過。需 executor＋排程開關。"""
    from core.scheduled_tasks import create_scheduled_task, list_scheduled_tasks

    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    existing = {
        task["template_id"] for task in list_scheduled_tasks(database=database).get("tasks", [])
    }
    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    for preset in DEFAULT_PRESETS:
        if preset["template_id"] in existing:
            skipped.append(preset["template_id"])
            continue
        result = create_scheduled_task(
            {
                "template_id": preset["template_id"],
                "params": dict(preset["params"]),
                "schedule_kind": preset["schedule_kind"],
                "run_time": preset["run_time"],
                "enabled": True,
            },
            database=database,
            cfg=cfg,
            now=now,
        )
        created.append(result.get("task") or result)
    return {
        "created": created,
        "already_present": skipped,
        "presets": [
            {k: v for k, v in preset.items() if k != "params"} | {"params": dict(preset["params"])}
            for preset in DEFAULT_PRESETS
        ],
        "claim_boundary": PACK_CLAIM_BOUNDARY,
    }


def presets_status(*, database: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    from core.scheduled_tasks import list_scheduled_tasks

    tasks = list_scheduled_tasks(database=database).get("tasks", [])
    present = {task["template_id"] for task in tasks}
    return {
        "morning_pack": MORNING_PACK_TEMPLATE in present,
        "evening_handoffs": ACTIVE_HANDOFFS_TEMPLATE in present,
        "all_present": all(p["template_id"] in present for p in DEFAULT_PRESETS),
    }


def build_today_view(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """儀表板「01 今天」用：上次做到哪＋早晨包摘要＋預設排程狀態。提案另由 /proposals 提供。"""
    from core.agent_executor import executor_enabled, l2_enabled
    from core.scheduled_tasks import scheduled_tasks_enabled

    cfg = cfg or get_config()
    now = now or get_local_now()
    if projects is None:
        from core.project_engine import get_active_projects_list

        projects = get_active_projects_list()
    top = projects[0] if projects else None
    resume = None
    if top:
        resume = {
            "project_key": top.get("project_key"),
            "display_name": top.get("display_name"),
            "category": top.get("category"),
            "last_activity_at": top.get("last_activity_at"),
            "last_action_summary": top.get("last_action_summary"),
            "open_loops_count": top.get("open_loops_count"),
            "local_path": top.get("local_path"),
            "github_url": top.get("github_url"),
        }
    pack = latest_pack_summary(database=database, now=now)
    try:
        presets = presets_status(database=database, now=now)
    except Exception as exc:  # noqa: BLE001 — 排程表讀不到也不該讓今日視圖消失
        presets = {"error": type(exc).__name__}
    memory: dict[str, Any] = {"enabled": False, "counts": {}, "total": 0}
    try:
        from core.secretary_memory import list_notes, memory_enabled

        if memory_enabled(cfg):
            listed = list_notes(limit=1, database=database)
            memory = {"enabled": True, "counts": listed["counts"], "total": listed["total"]}
    except Exception as exc:  # noqa: BLE001 — 記憶區讀不到也不該讓今日視圖消失
        memory = {"enabled": False, "error": type(exc).__name__, "counts": {}, "total": 0}
    return {
        "generated_at": (now.replace(tzinfo=None) if now.tzinfo else now).isoformat(timespec="seconds"),
        "resume": resume,
        "active_project_count": sum(1 for p in projects if p.get("status") == "active"),
        "pack": pack,
        "pack_line": pack_summary_line(pack),
        "memory": memory,
        "schedules": {
            "executor_enabled": executor_enabled(cfg),
            "scheduled_tasks_enabled": scheduled_tasks_enabled(cfg),
            "l2_enabled": l2_enabled(cfg),
            **presets,
        },
        "claim_boundary": "只彙整既有唯讀資料（專案狀態、最近一次早晨包收據、排程表）；不執行任何動作。",
    }
