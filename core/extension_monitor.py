"""Browser Extension ingestion 的可觀察狀態，不暴露 ingest token。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent
from core.security import extension_ingest_authorized, get_extension_ingest_token
from core.time_utils import get_local_now


SUPPORTED_BROWSER_PLATFORMS = (
    ("chatgpt", "ChatGPT", "watchers.browser.chatgpt", {"chatgpt", "chatgpt_web"}),
    ("gemini", "Gemini", "watchers.browser.gemini", {"gemini", "gemini_web"}),
    ("claude", "Claude.ai", "watchers.browser.claude_web", {"claude", "claude_web"}),
    ("manus", "Manus", "watchers.browser.manus", {"manus", "manus_web"}),
)


def build_extension_status(
    provided_token: str | None = None,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    database = database or get_db()
    now = now or get_local_now()
    today_start = datetime.combine(now.date(), time.min)
    tomorrow_start = today_start + timedelta(days=1)
    aliases = {
        alias
        for _, _, _, platform_aliases in SUPPORTED_BROWSER_PLATFORMS
        for alias in platform_aliases
    }

    with database.session_scope() as session:
        rows = (
            session.query(AIPromptEvent.platform, AIPromptEvent.timestamp)
            .filter(
                AIPromptEvent.platform.in_(aliases),
                AIPromptEvent.url.isnot(None),
                AIPromptEvent.url.like("http%"),
            )
            .all()
        )
        captured = [(str(row[0]).lower(), row[1]) for row in rows]

    platform_rows = []
    all_last_times = []
    for key, label, config_key, platform_aliases in SUPPORTED_BROWSER_PLATFORMS:
        event_times = [timestamp for platform, timestamp in captured if platform in platform_aliases]
        today_times = [
            timestamp
            for timestamp in event_times
            if today_start <= timestamp < tomorrow_start
        ]
        all_last_times.extend(event_times)
        platform_rows.append(
            {
                "key": key,
                "label": label,
                "enabled": bool(cfg.get(config_key, True)),
                "events_total": len(event_times),
                "events_today": len(today_times),
                "last_capture_at": (
                    max(event_times).isoformat(timespec="seconds") if event_times else None
                ),
                "observation_status": "observed" if event_times else "not_observed",
            }
        )

    token_configured = bool(get_extension_ingest_token(cfg))
    pairing_verified = extension_ingest_authorized(provided_token, cfg)
    total = len(captured)
    return {
        "service": {
            "status": "ok",
            "base_url": f"http://127.0.0.1:{int(cfg.get('server.port', 8765))}",
            "monitor_url": f"http://127.0.0.1:{int(cfg.get('server.port', 8765))}/extension-monitor",
        },
        "extension": {
            "role": "ingestion_bridge",
            "token_configured": token_configured,
            "pairing_verified": pairing_verified,
            "pairing_note": (
                "verified_for_this_request"
                if pairing_verified
                else "pairing_can_only_be_verified_with_extension_token"
            ),
            "events_total": total,
            "events_today": sum(item["events_today"] for item in platform_rows),
            "last_capture_at": (
                max(all_last_times).isoformat(timespec="seconds") if all_last_times else None
            ),
            "capture_status": "observed" if total else "configured_unverified",
        },
        "platforms": platform_rows,
        "claim_boundary": (
            "Enabled means configured. Observed means at least one browser event exists; "
            "neither proves complete capture coverage."
        ),
        "generated_at": now.isoformat(timespec="seconds"),
    }
