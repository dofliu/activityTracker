"""Browser Extension ingestion 的可觀察狀態，不暴露 ingest token 或對話內容。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
import re
from typing import Any

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent, BrowserExtensionHeartbeat
from core.security import extension_ingest_authorized, get_extension_ingest_token
from core.time_utils import get_local_now


SUPPORTED_BROWSER_PLATFORMS = (
    ("chatgpt", "ChatGPT", "watchers.browser.chatgpt", {"chatgpt", "chatgpt_web"}),
    ("gemini", "Gemini", "watchers.browser.gemini", {"gemini", "gemini_web"}),
    ("claude", "Claude.ai", "watchers.browser.claude_web", {"claude", "claude_web"}),
    ("manus", "Manus", "watchers.browser.manus", {"manus", "manus_web"}),
)
SUPPORTED_BROWSER_KEYS = {item[0] for item in SUPPORTED_BROWSER_PLATFORMS}
ALLOWED_CAPTURE_STATUSES = {
    "none",
    "content_ready",
    "prompt_detected",
    "attempting",
    "accepted",
    "queued_offline",
    "skipped_duplicate",
    "error",
}


def _local_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def record_extension_heartbeat(
    payload: dict[str, Any],
    *,
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """只保存經 API token gate 驗證後的非敏感 Extension 診斷 receipt。"""
    database = database or get_db()
    now = _local_naive(now or get_local_now())
    instance_id = str(payload.get("instance_id") or "").strip()
    extension_version = str(payload.get("extension_version") or "").strip()
    if not instance_id or len(instance_id) > 64:
        raise ValueError("invalid instance_id")
    if not extension_version or len(extension_version) > 32:
        raise ValueError("invalid extension_version")

    ready_platforms = sorted({
        str(item).strip().lower()
        for item in payload.get("ready_platforms", [])
        if str(item).strip().lower() in SUPPORTED_BROWSER_KEYS
    })
    capture_status = str(payload.get("last_capture_status") or "none").strip().lower()
    if capture_status not in ALLOWED_CAPTURE_STATUSES:
        capture_status = "error"
    error_code = str(payload.get("last_error_code") or "").strip().lower() or None
    if error_code and not re.fullmatch(r"[a-z0-9_.-]{1,80}", error_code):
        error_code = "invalid_error_code"
    offline_queue_size = max(0, min(int(payload.get("offline_queue_size") or 0), 100))
    last_capture_at = _local_naive(payload.get("last_capture_at"))

    with database.session_scope() as session:
        heartbeat = (
            session.query(BrowserExtensionHeartbeat)
            .filter(BrowserExtensionHeartbeat.instance_id == instance_id)
            .first()
        )
        if heartbeat is None:
            heartbeat = BrowserExtensionHeartbeat(
                instance_id=instance_id,
                extension_version=extension_version,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(heartbeat)
        heartbeat.extension_version = extension_version
        heartbeat.ready_platforms_json = json.dumps(ready_platforms, ensure_ascii=False)
        heartbeat.last_capture_status = capture_status
        heartbeat.last_capture_at = last_capture_at
        heartbeat.last_error_code = error_code
        heartbeat.offline_queue_size = offline_queue_size
        heartbeat.last_seen_at = now

    return {
        "status": "accepted",
        "server_received_at": now.isoformat(timespec="seconds"),
        "privacy_boundary": "no_token_no_url_no_prompt_no_response",
    }


def build_extension_status(
    provided_token: str | None = None,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    database = database or get_db()
    now = _local_naive(now or get_local_now())
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
        heartbeat_row = (
            session.query(BrowserExtensionHeartbeat)
            .order_by(BrowserExtensionHeartbeat.last_seen_at.desc())
            .first()
        )
        heartbeat = (
            {
                "extension_version": heartbeat_row.extension_version,
                "ready_platforms_json": heartbeat_row.ready_platforms_json,
                "last_capture_status": heartbeat_row.last_capture_status,
                "last_capture_at": heartbeat_row.last_capture_at,
                "last_error_code": heartbeat_row.last_error_code,
                "offline_queue_size": heartbeat_row.offline_queue_size,
                "last_seen_at": heartbeat_row.last_seen_at,
            }
            if heartbeat_row is not None
            else None
        )

    heartbeat_stale_minutes = max(
        1,
        min(int(cfg.get("watchers.browser.heartbeat_stale_minutes", 5)), 60),
    )
    heartbeat_age_seconds = None
    heartbeat_recent = False
    ready_platforms: set[str] = set()
    if heartbeat is not None:
        last_seen = _local_naive(heartbeat["last_seen_at"])
        heartbeat_age_seconds = max(0, int((now - last_seen).total_seconds()))
        heartbeat_recent = heartbeat_age_seconds <= heartbeat_stale_minutes * 60
        try:
            ready_platforms = {
                str(item).lower()
                for item in json.loads(heartbeat["ready_platforms_json"] or "[]")
                if str(item).lower() in SUPPORTED_BROWSER_KEYS
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            ready_platforms = set()

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
                "content_script_seen": key in ready_platforms,
            }
        )

    token_configured = bool(get_extension_ingest_token(cfg))
    pairing_verified = extension_ingest_authorized(provided_token, cfg)
    total = len(captured)
    if not token_configured:
        connection_status = "not_configured"
    elif pairing_verified:
        connection_status = "verified_for_this_request"
    elif heartbeat_recent:
        connection_status = "recent_verified_heartbeat"
    elif heartbeat is not None:
        connection_status = "stale_verified_heartbeat"
    else:
        connection_status = "unverified"

    if total:
        capture_status = "observed"
    elif heartbeat_recent:
        capture_status = "paired_waiting_event"
    elif token_configured:
        capture_status = "configured_unverified"
    else:
        capture_status = "not_configured"

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
            "heartbeat_verified": heartbeat_recent,
            "connection_status": connection_status,
            "pairing_note": (
                "verified_for_this_request"
                if pairing_verified
                else (
                    "recent_token_authenticated_heartbeat"
                    if heartbeat_recent
                    else "pairing_not_recently_observed"
                )
            ),
            "last_heartbeat_at": (
                _local_naive(heartbeat["last_seen_at"]).isoformat(timespec="seconds")
                if heartbeat is not None
                else None
            ),
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "heartbeat_stale_after_seconds": heartbeat_stale_minutes * 60,
            "extension_version": heartbeat["extension_version"] if heartbeat else None,
            "ready_platforms": sorted(ready_platforms),
            "last_capture_status": heartbeat["last_capture_status"] if heartbeat else "none",
            "last_capture_attempt_at": (
                _local_naive(heartbeat["last_capture_at"]).isoformat(timespec="seconds")
                if heartbeat is not None and heartbeat["last_capture_at"] is not None
                else None
            ),
            "last_error_code": heartbeat["last_error_code"] if heartbeat else None,
            "offline_queue_size": heartbeat["offline_queue_size"] if heartbeat else 0,
            "events_total": total,
            "events_today": sum(item["events_today"] for item in platform_rows),
            "last_capture_at": (
                max(all_last_times).isoformat(timespec="seconds") if all_last_times else None
            ),
            "capture_status": capture_status,
        },
        "platforms": platform_rows,
        "claim_boundary": (
            "A recent heartbeat proves a token-authenticated Extension reached the local service. "
            "Observed means at least one browser event exists; neither proves complete coverage."
        ),
        "generated_at": now.isoformat(timespec="seconds"),
    }
