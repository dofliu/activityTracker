"""可信的前景使用時間聚合與每日里程碑判定。

Duration 只來自 WindowEvent；AI events 僅計互動次數。這個邊界可避免把
prompt/session timestamps 誤推成實際使用時間。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.exc import IntegrityError

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent, MilestoneNotificationReceipt, WindowEvent
from core.time_utils import get_local_now


DEFAULT_INTERFACE_RULES: tuple[dict[str, Any], ...] = (
    {"name": "Codex", "app_contains": ["codex"], "title_contains": ["codex"]},
    {
        "name": "Claude Code",
        "app_contains": ["claude-code", "claude_code"],
        "title_contains": ["claude code"],
    },
    {
        "name": "ChatGPT",
        "app_contains": ["chatgpt"],
        "title_contains": ["chatgpt", "chat.openai.com"],
    },
    {
        "name": "Claude",
        "app_contains": ["claude"],
        "title_contains": ["claude.ai", "claude"],
    },
    {"name": "Gemini", "app_contains": ["gemini"], "title_contains": ["gemini"]},
    {
        "name": "Antigravity",
        "app_contains": ["antigravity"],
        "title_contains": ["antigravity"],
    },
    {
        "name": "VS Code",
        "app_contains": ["code.exe", "visual studio code"],
        "title_contains": ["visual studio code"],
    },
    {
        "name": "Terminal",
        "app_contains": ["windowsterminal", "powershell", "terminal", "cmd.exe"],
        "title_contains": [],
    },
    {
        "name": "Browser",
        "app_contains": ["chrome", "msedge", "firefox", "brave"],
        "title_contains": [],
    },
)

DEFAULT_GOAL_INTERFACES = (
    "Claude Code",
    "Claude",
    "Codex",
    "ChatGPT",
    "Gemini",
    "Antigravity",
)

PLATFORM_INTERFACE_MAP = {
    "codex": "Codex",
    "claude_code": "Claude Code",
    "claude_desktop": "Claude",
    "claude": "Claude",
    "claude_web": "Claude",
    "chatgpt": "ChatGPT",
    "chatgpt_web": "ChatGPT",
    "gemini": "Gemini",
    "gemini_web": "Gemini",
    "antigravity": "Antigravity",
}


@dataclass(frozen=True)
class UsageInterval:
    event_id: int
    start: datetime
    end: datetime
    interface: str
    app_name: str
    window_title: str


def _value(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(key, default)
    return getattr(event, key, default)


def _clean_terms(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value).strip().lower() for value in values if str(value).strip()]


def interface_rules_from_config(cfg: Any | None = None) -> list[dict[str, Any]]:
    cfg = cfg or get_config()
    raw_rules = cfg.get("usage_tracking.interface_rules", None)
    if not isinstance(raw_rules, list):
        raw_rules = list(DEFAULT_INTERFACE_RULES)

    rules: list[dict[str, Any]] = []
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        rules.append(
            {
                "name": name,
                "app_contains": _clean_terms(raw.get("app_contains", [])),
                "title_contains": _clean_terms(raw.get("title_contains", [])),
            }
        )
    return rules or list(DEFAULT_INTERFACE_RULES)


def classify_interface(
    app_name: str | None,
    window_title: str | None,
    rules: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """先比對 title，再比對 process，避免 ChatGPT.exe 中的 Codex 視窗被誤歸類。"""
    normalized_app = str(app_name or "").lower()
    normalized_title = str(window_title or "").lower()
    selected_rules = rules or DEFAULT_INTERFACE_RULES

    for rule in selected_rules:
        terms = _clean_terms(rule.get("title_contains", []))
        if any(term in normalized_title for term in terms):
            return str(rule.get("name"))
    for rule in selected_rules:
        terms = _clean_terms(rule.get("app_contains", []))
        if any(term in normalized_app for term in terms):
            return str(rule.get("name"))
    return "Other"


def _normalize_interval(
    event: Any,
    index: int,
    range_start: datetime,
    range_end: datetime,
    rules: Sequence[Mapping[str, Any]],
    max_interval_seconds: int,
) -> UsageInterval | None:
    start = _value(event, "start_time")
    end = _value(event, "end_time")
    if not isinstance(start, datetime):
        return None

    if not isinstance(end, datetime) or end <= start:
        try:
            duration = max(0.0, float(_value(event, "duration_seconds", 0.0)))
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            return None
        end = start + timedelta(seconds=duration)

    trusted_end = min(end, start + timedelta(seconds=max_interval_seconds))
    clipped_start = max(start, range_start)
    clipped_end = min(trusted_end, range_end)
    if clipped_end <= clipped_start:
        return None

    app_name = str(_value(event, "app_name", "") or "")
    window_title = str(_value(event, "window_title", "") or "")
    return UsageInterval(
        event_id=int(_value(event, "id", index) or index),
        start=clipped_start,
        end=clipped_end,
        interface=classify_interface(app_name, window_title, rules),
        app_name=app_name,
        window_title=window_title,
    )


def aggregate_window_events(
    events: Iterable[Any],
    range_start: datetime,
    range_end: datetime,
    rules: Sequence[Mapping[str, Any]] | None = None,
    max_interval_seconds: int = 3600,
) -> dict[str, dict[str, Any]]:
    """去除 exact duplicate 與跨介面 overlap，確保任一秒最多歸屬一個前景介面。"""
    selected_rules = list(rules or DEFAULT_INTERFACE_RULES)
    normalized: list[UsageInterval] = []
    seen: set[tuple[Any, ...]] = set()

    for index, event in enumerate(events, start=1):
        interval = _normalize_interval(
            event,
            index,
            range_start,
            range_end,
            selected_rules,
            max(1, int(max_interval_seconds)),
        )
        if not interval:
            continue
        duplicate_key = (
            interval.start,
            interval.end,
            interval.app_name.lower(),
            interval.window_title,
        )
        if duplicate_key in seen:
            continue
        seen.add(duplicate_key)
        normalized.append(interval)

    result: dict[str, dict[str, Any]] = {}
    for interval in normalized:
        item = result.setdefault(
            interval.interface,
            {"foreground_seconds": 0.0, "event_count": 0, "last_activity_at": None},
        )
        item["event_count"] += 1
        if item["last_activity_at"] is None or interval.end > item["last_activity_at"]:
            item["last_activity_at"] = interval.end

    boundaries = sorted({point for item in normalized for point in (item.start, item.end)})
    for segment_start, segment_end in zip(boundaries, boundaries[1:]):
        if segment_end <= segment_start:
            continue
        active = [
            item
            for item in normalized
            if item.start <= segment_start and item.end >= segment_end
        ]
        if not active:
            continue
        # 重疊時以較晚開始、較新 event id 的前景事件為準。
        chosen = max(active, key=lambda item: (item.start, item.event_id))
        result[chosen.interface]["foreground_seconds"] += (
            segment_end - segment_start
        ).total_seconds()

    return result


def _parse_target_date(value: date | str | None, *, default_date: date | None = None) -> date:
    if value is None:
        return default_date or get_local_now().date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _positive_ints(values: Any, default: Sequence[int]) -> list[int]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = default
    parsed: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            parsed.add(number)
    return sorted(parsed) or sorted(set(default))


def get_usage_summary(
    target_date: date | str | None = None,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    manager_status: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    database = database or get_db()
    now = now or get_local_now()
    local_date = _parse_target_date(target_date, default_date=now.date())
    range_start = datetime.combine(local_date, time.min)
    range_end = range_start + timedelta(days=1)
    rules = interface_rules_from_config(cfg)
    max_interval_seconds = int(
        cfg.get("usage_tracking.max_interval_seconds", 3600) or 3600
    )

    with database.session_scope() as session:
        window_rows = (
            session.query(WindowEvent)
            .filter(WindowEvent.start_time < range_end, WindowEvent.end_time > range_start)
            .order_by(WindowEvent.start_time.asc(), WindowEvent.id.asc())
            .all()
        )
        window_events = [
            {
                "id": row.id,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "duration_seconds": row.duration_seconds,
                "app_name": row.app_name,
                "window_title": row.window_title,
            }
            for row in window_rows
        ]
        ai_rows = (
            session.query(AIPromptEvent.platform)
            .filter(
                AIPromptEvent.timestamp >= range_start,
                AIPromptEvent.timestamp < range_end,
                AIPromptEvent.turn_key.isnot(None),
            )
            .all()
        )
        ai_platforms = [str(row[0]) for row in ai_rows]
        receipt_rows = (
            session.query(MilestoneNotificationReceipt)
            .filter(MilestoneNotificationReceipt.local_date == local_date.isoformat())
            .all()
        )
        receipt_map = {
            int(row.milestone_minutes): str(row.status) for row in receipt_rows
        }

    aggregated = aggregate_window_events(
        window_events,
        range_start,
        range_end,
        rules=rules,
        max_interval_seconds=max_interval_seconds,
    )
    interactions: dict[str, int] = {}
    for platform_value in ai_platforms:
        platform = platform_value.lower()
        interface = PLATFORM_INTERFACE_MAP.get(platform, platform.replace("_", " ").title())
        interactions[interface] = interactions.get(interface, 0) + 1

    interface_names = set(aggregated) | set(interactions)
    interfaces: list[dict[str, Any]] = []
    for name in interface_names:
        observed = aggregated.get(name, {})
        seconds = round(float(observed.get("foreground_seconds", 0.0)), 3)
        interfaces.append(
            {
                "name": name,
                "foreground_seconds": seconds,
                "foreground_minutes": round(seconds / 60.0, 1),
                "ai_interaction_count": interactions.get(name, 0),
                "window_event_count": int(observed.get("event_count", 0)),
                "last_activity_at": _format_datetime(observed.get("last_activity_at")),
            }
        )
    interfaces.sort(
        key=lambda item: (
            -float(item["foreground_seconds"]),
            -int(item["ai_interaction_count"]),
            str(item["name"]),
        )
    )

    enabled = bool(cfg.get("usage_tracking.enabled", False))
    window_enabled = bool(cfg.get("watchers.window_watcher.enabled", True))
    supported = sys.platform == "win32"
    if not enabled or not window_enabled or not supported:
        coverage_status = "unavailable"
        if not enabled:
            coverage_note = "usage_tracking_disabled"
        elif not window_enabled:
            coverage_note = "window_collector_disabled"
        else:
            coverage_note = "window_collector_not_supported_on_platform"
    else:
        coverage_status = "partial"
        coverage_note = "continuous_coverage_ledger_not_available"
        if manager_status:
            runtime = (manager_status.get("collector_runtime") or {}).get("window_watcher")
            health = (manager_status.get("collector_health") or {}).get("window_watcher")
            if runtime != "running" or health not in {"healthy", "idle"}:
                coverage_note = f"collector_{runtime or 'unknown'}_{health or 'unknown'}"

    configured_goal_interfaces = cfg.get(
        "usage_tracking.goal_interfaces", list(DEFAULT_GOAL_INTERFACES)
    )
    if not isinstance(configured_goal_interfaces, list):
        configured_goal_interfaces = list(DEFAULT_GOAL_INTERFACES)
    goal_interfaces = [str(value) for value in configured_goal_interfaces]
    goal_seconds = sum(
        float(item["foreground_seconds"])
        for item in interfaces
        if item["name"] in goal_interfaces
    )
    daily_goal_minutes = max(
        1, int(cfg.get("usage_tracking.daily_goal_minutes", 360) or 360)
    )
    milestones = _positive_ints(
        cfg.get("usage_tracking.milestones_minutes", [120, 240, 360]),
        [120, 240, 360],
    )
    milestone_state = [
        {
            "minutes": threshold,
            "reached": goal_seconds >= threshold * 60,
            "notification_status": receipt_map.get(threshold),
        }
        for threshold in milestones
    ]
    total_seconds = sum(float(item["foreground_seconds"]) for item in interfaces)
    last_values = [
        datetime.fromisoformat(item["last_activity_at"])
        for item in interfaces
        if item["last_activity_at"]
    ]

    return {
        "date": local_date.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "metric_label": "foreground_active_time",
        "claim_boundary": "Observed foreground time; not productivity or actual work hours.",
        "coverage_status": coverage_status,
        "coverage_note": coverage_note,
        "data_updated_at": _format_datetime(max(last_values) if last_values else None),
        "observed_total_seconds": round(total_seconds, 3),
        "observed_total_minutes": round(total_seconds / 60.0, 1),
        "goal": {
            "label": str(cfg.get("usage_tracking.goal_label", "AI 協作")),
            "interfaces": goal_interfaces,
            "foreground_seconds": round(goal_seconds, 3),
            "foreground_minutes": round(goal_seconds / 60.0, 1),
            "daily_goal_minutes": daily_goal_minutes,
            "progress_percent": round(goal_seconds / (daily_goal_minutes * 60) * 100, 1),
        },
        "milestones": milestone_state,
        "interfaces": interfaces,
    }


def is_quiet_hours(now: datetime, start_text: str, end_text: str) -> bool:
    try:
        start_value = datetime.strptime(start_text, "%H:%M").time()
        end_value = datetime.strptime(end_text, "%H:%M").time()
    except (TypeError, ValueError):
        return False
    current = now.time()
    if start_value == end_value:
        return False
    if start_value < end_value:
        return start_value <= current < end_value
    return current >= start_value or current < end_value


def build_milestone_message(
    summary: Mapping[str, Any],
    milestone_minutes: int,
    tone: str,
) -> str:
    goal = summary.get("goal") or {}
    label = str(goal.get("label") or "主要介面")
    duration = (
        f"{milestone_minutes // 60} 小時"
        if milestone_minutes % 60 == 0
        else f"{milestone_minutes} 分鐘"
    )
    lower_bound = "已記錄至少" if summary.get("coverage_status") == "partial" else "已達"
    prefix = f"今天 {label} 前景使用時間{lower_bound} {duration}"
    normalized_tone = str(tone or "encouraging").lower()
    if normalized_tone == "neutral":
        return prefix + "。"
    if normalized_tone == "praise":
        return prefix + "，今日里程碑完成，做得很好。"
    return prefix + "，進度保持得很好，繼續加油。"


def evaluate_daily_milestones(
    target_date: date | str | None = None,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    manager_status: Mapping[str, Any] | None = None,
    notifier: Any | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    database = database or get_db()
    now = now or get_local_now()
    local_date = _parse_target_date(target_date, default_date=now.date())

    if local_date != now.date():
        return {"status": "skipped", "reason": "milestones_only_evaluate_current_day"}
    if not bool(cfg.get("usage_tracking.enabled", False)):
        return {"status": "disabled", "reason": "usage_tracking_disabled"}
    if not bool(cfg.get("usage_tracking.notifications.enabled", False)):
        return {"status": "disabled", "reason": "milestone_notifications_disabled"}

    summary = get_usage_summary(
        local_date,
        database=database,
        cfg=cfg,
        manager_status=manager_status,
        now=now,
    )
    if summary["coverage_status"] == "unavailable":
        return {"status": "skipped", "reason": summary["coverage_note"], "summary": summary}

    quiet_start = str(cfg.get("usage_tracking.notifications.quiet_hours_start", "22:00"))
    quiet_end = str(cfg.get("usage_tracking.notifications.quiet_hours_end", "08:00"))
    if is_quiet_hours(now, quiet_start, quiet_end):
        return {"status": "quiet_hours", "summary": summary}

    reached = [item["minutes"] for item in summary["milestones"] if item["reached"]]
    if not reached:
        return {"status": "not_reached", "summary": summary}

    channel = "desktop"
    with database.session_scope() as session:
        receipt_rows = (
            session.query(MilestoneNotificationReceipt)
            .filter(
                MilestoneNotificationReceipt.local_date == local_date.isoformat(),
                MilestoneNotificationReceipt.channel == channel,
            )
            .all()
        )
        existing = {int(row.milestone_minutes) for row in receipt_rows}
        sent_times = [
            row.notified_at
            for row in receipt_rows
            if row.status == "sent" and row.notified_at is not None
        ]
        last_sent_at = max(sent_times) if sent_times else None
    pending = sorted(set(reached) - existing)
    if not pending:
        return {"status": "already_notified", "summary": summary}

    cooldown_minutes = max(
        0,
        int(cfg.get("usage_tracking.notifications.cooldown_minutes", 60) or 0),
    )
    if last_sent_at and now - last_sent_at < timedelta(minutes=cooldown_minutes):
        return {
            "status": "cooldown",
            "retry_after": (
                last_sent_at + timedelta(minutes=cooldown_minutes)
            ).isoformat(timespec="seconds"),
            "pending_milestones": pending,
            "summary": summary,
        }

    selected = max(pending)
    tone = str(cfg.get("usage_tracking.notifications.tone", "encouraging"))
    message = build_milestone_message(summary, selected, tone)
    if dry_run:
        return {
            "status": "dry_run",
            "milestone_minutes": selected,
            "message": message,
            "summary": summary,
        }

    if notifier is None:
        from notifiers.desktop_notifier import DesktopNotifier

        notifier = DesktopNotifier()
    if not notifier.send_usage_milestone(summary, selected, message):
        return {
            "status": "notification_failed",
            "milestone_minutes": selected,
            "message": message,
            "summary": summary,
        }

    try:
        with database.session_scope() as session:
            for threshold in pending:
                session.add(
                    MilestoneNotificationReceipt(
                        local_date=local_date.isoformat(),
                        milestone_minutes=threshold,
                        channel=channel,
                        interface_group=str(summary["goal"]["label"]),
                        observed_minutes=float(summary["goal"]["foreground_minutes"]),
                        status="sent" if threshold == selected else "coalesced",
                        message=message if threshold == selected else None,
                        notified_at=now,
                    )
                )
    except IntegrityError:
        return {"status": "already_notified", "summary": summary}

    return {
        "status": "notified",
        "milestone_minutes": selected,
        "message": message,
        "coalesced_milestones": [value for value in pending if value != selected],
        "summary": summary,
    }
