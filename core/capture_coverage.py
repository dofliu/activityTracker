"""統一呈現 Desktop focus、Browser capture 與本機 transcript 的獨立 coverage。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_

from core.config import get_config
from core.database import get_db
from core.desktop_sources import (
    claude_desktop_cloud_cache_detected,
    default_claude_desktop_data_dir,
    default_claude_desktop_logs_dir,
    has_claude_desktop_project_logs,
)
from core.models import AIPromptEvent, WindowEvent
from core.time_utils import get_local_now
from core.usage_analytics import aggregate_window_events, interface_rules_from_config


PLATFORM_ROWS = (
    {"key": "chatgpt", "label": "ChatGPT", "interface": "ChatGPT", "web": {"chatgpt", "chatgpt_web"}, "transcript": None},
    {"key": "claude", "label": "Claude", "interface": "Claude", "web": {"claude", "claude_web"}, "transcript": "claude_desktop"},
    {"key": "codex", "label": "Codex", "interface": "Codex", "web": set(), "transcript": "codex"},
    {"key": "gemini", "label": "Gemini", "interface": "Gemini", "web": {"gemini", "gemini_web"}, "transcript": None},
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    last = max((event["timestamp"] for event in events if event["timestamp"]), default=None)
    return {
        "turns_today": len(events),
        "responses_today": sum(1 for event in events if event["has_response"]),
        "last_seen_at": _iso(last),
    }


def build_capture_coverage(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    claude_data_dir: Path | None = None,
    claude_logs_dir: Path | None = None,
) -> dict[str, Any]:
    """回傳非敏感 coverage 摘要；不包含 prompt、response、URL 或本機絕對路徑。"""
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    day_start = datetime.combine(now.date(), time.min)
    day_end = day_start + timedelta(days=1)

    with database.session_scope() as session:
        windows = (
            session.query(WindowEvent)
            .filter(
                WindowEvent.start_time < day_end,
                or_(WindowEvent.end_time > day_start, WindowEvent.end_time.is_(None)),
            )
            .all()
        )
        today_rows = (
            session.query(AIPromptEvent)
            .filter(AIPromptEvent.timestamp >= day_start, AIPromptEvent.timestamp < day_end)
            .all()
        )
        today_ai = [
            {
                "platform": event.platform.lower(),
                "has_url": bool(event.url),
                "has_response": bool(event.response_text),
                "timestamp": event.timestamp,
            }
            for event in today_rows
        ]
        transcript_totals = {
            platform: session.query(AIPromptEvent)
            .filter(AIPromptEvent.platform == platform, AIPromptEvent.source_path.isnot(None))
            .count()
            for platform in ("claude_desktop", "codex")
        }
        focus = aggregate_window_events(
            windows,
            day_start,
            min(day_end, now),
            rules=interface_rules_from_config(cfg),
        )
    data_dir = claude_data_dir or default_claude_desktop_data_dir()
    logs_dir = claude_logs_dir or default_claude_desktop_logs_dir()
    needs_claude_probe = transcript_totals.get("claude_desktop", 0) == 0
    desktop_logs_available = needs_claude_probe and has_claude_desktop_project_logs(logs_dir)
    cloud_cache_available = (
        needs_claude_probe
        and not desktop_logs_available
        and claude_desktop_cloud_cache_detected(data_dir)
    )

    platforms: list[dict[str, Any]] = []
    for definition in PLATFORM_ROWS:
        observed_focus = focus.get(definition["interface"], {})
        focus_seconds = round(float(observed_focus.get("foreground_seconds", 0.0)), 3)
        web_events = [
            event
            for event in today_ai
            if event["platform"] in definition["web"] and event["has_url"]
        ]
        transcript_platform = definition["transcript"]
        transcript_events = [
            event
            for event in today_ai
            if transcript_platform and event["platform"] == transcript_platform
        ]

        if not definition["web"]:
            web_channel = {"state": "not_applicable", **_event_summary([])}
        else:
            web_channel = {
                "state": "observed" if web_events else "waiting",
                **_event_summary(web_events),
            }

        if transcript_platform is None:
            transcript_channel = {
                "state": "unsupported",
                "turns_today": 0,
                "responses_today": 0,
                "last_seen_at": None,
            }
        else:
            transcript_summary = _event_summary(transcript_events)
            if transcript_totals.get(transcript_platform, 0) > 0:
                transcript_state = "observed"
            elif transcript_platform == "claude_desktop" and desktop_logs_available:
                transcript_state = "available_waiting"
            elif transcript_platform == "claude_desktop" and cloud_cache_available:
                transcript_state = "cache_detected_unparsed"
            else:
                transcript_state = "waiting"
            transcript_channel = {"state": transcript_state, **transcript_summary}

        platforms.append(
            {
                "key": definition["key"],
                "label": definition["label"],
                "desktop_focus": {
                    "state": "observed" if focus_seconds > 0 else "waiting",
                    "foreground_seconds_today": focus_seconds,
                    "events_today": int(observed_focus.get("event_count", 0)),
                    "last_seen_at": _iso(observed_focus.get("last_activity_at")),
                },
                "web_capture": web_channel,
                "transcript_capture": transcript_channel,
            }
        )

    return {
        "as_of": now.isoformat(),
        "platforms": platforms,
        "claim_boundary": (
            "desktop_focus_is_time_only; web_capture_requires_extension; "
            "claude_desktop_transcript_covers_cowork_local_agent_only; cloud_chat_cache_is_not_parsed"
        ),
    }
