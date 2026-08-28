"""可驗證的本機 agent 背景任務 receipt 與每日統計。

前景時間仍完全由 ``WindowEvent`` 計算；本模組只接受本機 session log
可確認的 user prompt start 與明確 final completion，因此兩類數字不會混算。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.config import get_config
from core.database import get_db
from core.models import BackgroundTaskRun
from core.time_utils import get_local_now


DEFAULT_PLATFORMS = ("claude_code", "claude_desktop", "codex")
PLATFORM_LABELS = {
    "claude_code": "Claude Code",
    "claude_desktop": "Claude Desktop Agent",
    "codex": "Codex",
}
COMPLETED_STATUS = "completed"
AWAITING_FINAL_STATUS = "awaiting_final"
UNTRUSTED_DURATION_STATUS = "untrusted_duration"


@dataclass(frozen=True)
class BackgroundTaskEvidence:
    """同一個 task 的可追溯 start receipt 與可選 completion receipt。"""

    platform: str
    source_path: str
    started_at: datetime
    start_position: int | None
    session_id: str | None = None
    cwd: str | None = None
    project_tag: str | None = None
    completed_at: datetime | None = None
    end_position: int | None = None
    completion_evidence_kind: str | None = None


def _as_platforms(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return DEFAULT_PLATFORMS
    cleaned = tuple(str(item).strip().lower() for item in value if str(item).strip())
    return cleaned or DEFAULT_PLATFORMS


def background_tracking_enabled(platform: str, cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    if not bool(cfg.get("background_task_tracking.enabled", True)):
        return False
    return platform.strip().lower() in _as_platforms(
        cfg.get("background_task_tracking.platforms", DEFAULT_PLATFORMS)
    )


def max_task_duration_seconds(cfg: Any | None = None) -> int:
    cfg = cfg or get_config()
    raw = cfg.get("background_task_tracking.max_task_duration_seconds", 8 * 60 * 60)
    try:
        return max(60, int(raw))
    except (TypeError, ValueError):
        return 8 * 60 * 60


def task_key_for(platform: str, source_path: str, start_position: int | None) -> str:
    """以來源列位置建立穩定 identity，讓重掃不會增加重複任務。"""
    raw = f"background-task|{platform.lower()}|{source_path}|{start_position or 0}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def project_tag_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    value = str(cwd).strip()
    if not value:
        return None
    if "Documents" in value and "Codex" in value:
        return "Codex Automations"
    return Path(value).name or value


def _validated_status(
    evidence: BackgroundTaskEvidence,
    *,
    maximum_seconds: int,
) -> tuple[str, float | None]:
    if evidence.completed_at is None:
        return AWAITING_FINAL_STATUS, None
    seconds = (evidence.completed_at - evidence.started_at).total_seconds()
    if seconds <= 0 or seconds > maximum_seconds:
        return UNTRUSTED_DURATION_STATUS, None
    return COMPLETED_STATUS, float(seconds)


def record_background_task_evidence(
    evidence: BackgroundTaskEvidence,
    *,
    database=None,
    cfg: Any | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """寫入或更新 receipt；未成對／異常 duration 一律不提供可加總秒數。"""
    cfg = cfg or get_config()
    platform = evidence.platform.strip().lower()
    if not background_tracking_enabled(platform, cfg):
        return None
    if not isinstance(evidence.started_at, datetime):
        return None

    database = database or get_db()
    maximum_seconds = max_task_duration_seconds(cfg)
    status, duration_seconds = _validated_status(evidence, maximum_seconds=maximum_seconds)
    key = task_key_for(platform, evidence.source_path, evidence.start_position)
    now = observed_at or get_local_now()

    with database.session_scope() as session:
        row = session.query(BackgroundTaskRun).filter(BackgroundTaskRun.task_key == key).first()
        if row is None:
            row = BackgroundTaskRun(
                task_key=key,
                platform=platform,
                session_id=evidence.session_id,
                project_tag=evidence.project_tag or project_tag_from_cwd(evidence.cwd),
                cwd=evidence.cwd,
                started_at=evidence.started_at,
                completed_at=evidence.completed_at if status != AWAITING_FINAL_STATUS else None,
                duration_seconds=duration_seconds,
                status=status,
                start_evidence_kind="user_prompt",
                completion_evidence_kind=(
                    evidence.completion_evidence_kind if status == COMPLETED_STATUS else None
                ),
                source_path=evidence.source_path,
                start_position=evidence.start_position,
                end_position=evidence.end_position if status == COMPLETED_STATUS else None,
                observed_at=now,
            )
            session.add(row)
        else:
            # 不以較晚一次的 partial 掃描覆蓋已確認的 final receipt。
            row.observed_at = now
            row.session_id = evidence.session_id or row.session_id
            row.project_tag = evidence.project_tag or project_tag_from_cwd(evidence.cwd) or row.project_tag
            row.cwd = evidence.cwd or row.cwd
            if status == COMPLETED_STATUS:
                row.completed_at = evidence.completed_at
                row.duration_seconds = duration_seconds
                row.status = status
                row.completion_evidence_kind = evidence.completion_evidence_kind
                row.end_position = evidence.end_position
            elif row.status != COMPLETED_STATUS:
                row.completed_at = evidence.completed_at if status == UNTRUSTED_DURATION_STATUS else None
                row.duration_seconds = None
                row.status = status
                row.completion_evidence_kind = None
                row.end_position = evidence.end_position if status == UNTRUSTED_DURATION_STATUS else None

        return {
            "task_key": key,
            "status": row.status,
            "duration_seconds": row.duration_seconds,
        }


def _target_date(value: date | str | None, *, now: datetime) -> date:
    if value is None:
        return now.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _clip_interval(
    started_at: datetime,
    completed_at: datetime,
    range_start: datetime,
    range_end: datetime,
) -> tuple[datetime, datetime] | None:
    start = max(started_at, range_start)
    end = min(completed_at, range_end)
    return (start, end) if end > start else None


def _union_seconds(intervals: Iterable[tuple[datetime, datetime]]) -> float:
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    if not ordered:
        return 0.0
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += (current_end - current_start).total_seconds()
        current_start, current_end = start, end
    total += (current_end - current_start).total_seconds()
    return total


def get_background_task_summary(
    target_date: date | str | None = None,
    *,
    database=None,
    cfg: Any | None = None,
    now: datetime | None = None,
    recent_limit: int = 8,
) -> dict[str, Any]:
    """回傳已完成 receipt 的獨立背景執行時間；不與 foreground 合併。"""
    cfg = cfg or get_config()
    now = now or get_local_now()
    selected_date = _target_date(target_date, now=now)
    range_start = datetime.combine(selected_date, time.min)
    range_end = range_start + timedelta(days=1)
    database = database or get_db()
    enabled = bool(cfg.get("background_task_tracking.enabled", True))

    with database.session_scope() as session:
        completed = (
            session.query(BackgroundTaskRun)
            .filter(
                BackgroundTaskRun.status == COMPLETED_STATUS,
                BackgroundTaskRun.started_at < range_end,
                BackgroundTaskRun.completed_at > range_start,
            )
            .order_by(BackgroundTaskRun.completed_at.desc(), BackgroundTaskRun.id.desc())
            .all()
        )
        awaiting_count = (
            session.query(BackgroundTaskRun)
            .filter(
                BackgroundTaskRun.status == AWAITING_FINAL_STATUS,
                BackgroundTaskRun.started_at >= range_start,
                BackgroundTaskRun.started_at < range_end,
            )
            .count()
        )
        untrusted_count = (
            session.query(BackgroundTaskRun)
            .filter(
                BackgroundTaskRun.status == UNTRUSTED_DURATION_STATUS,
                BackgroundTaskRun.started_at >= range_start,
                BackgroundTaskRun.started_at < range_end,
            )
            .count()
        )

        all_intervals: list[tuple[datetime, datetime]] = []
        per_platform: dict[str, list[tuple[datetime, datetime]]] = {}
        for row in completed:
            if not row.completed_at:
                continue
            clipped = _clip_interval(row.started_at, row.completed_at, range_start, range_end)
            if not clipped:
                continue
            all_intervals.append(clipped)
            per_platform.setdefault(row.platform, []).append(clipped)

        interfaces = [
            {
                "platform": platform,
                "label": PLATFORM_LABELS.get(platform, platform),
                "verified_seconds": round(_union_seconds(intervals), 3),
                "completed_tasks": sum(1 for row in completed if row.platform == platform),
            }
            for platform, intervals in per_platform.items()
        ]
        interfaces.sort(key=lambda item: (-item["verified_seconds"], item["label"]))
        recent_tasks = [
            {
                "platform": row.platform,
                "label": PLATFORM_LABELS.get(row.platform, row.platform),
                "project_tag": row.project_tag,
                "started_at": row.started_at.isoformat(timespec="seconds"),
                "completed_at": row.completed_at.isoformat(timespec="seconds") if row.completed_at else None,
                "duration_seconds": row.duration_seconds,
                "status": row.status,
                "completion_evidence_kind": row.completion_evidence_kind,
            }
            for row in completed[:max(1, min(int(recent_limit), 20))]
        ]

    verified_seconds = round(_union_seconds(all_intervals), 3)
    return {
        "date": selected_date.isoformat(),
        "enabled": enabled,
        "metric_label": "verified_background_agent_execution_time",
        "claim_boundary": (
            "Only paired local agent task receipts with a prompt start and explicit final completion. "
            "Not foreground time, generic terminal time, productivity, or total computer work."
        ),
        "evidence_status": "verified_receipts" if completed else "not_observed",
        "verified_seconds": verified_seconds,
        "verified_minutes": round(verified_seconds / 60.0, 1),
        "completed_task_count": len(completed),
        "awaiting_final_count": awaiting_count,
        "untrusted_duration_count": untrusted_count,
        "interfaces": interfaces,
        "recent_tasks": recent_tasks,
        "max_task_duration_seconds": max_task_duration_seconds(cfg),
        "data_updated_at": now.isoformat(timespec="seconds"),
    }
