"""Browser Extension live verification 的短期、非敏感、fail-closed receipt。"""

from __future__ import annotations

from datetime import datetime, timedelta
import threading
from typing import Any, Callable
from uuid import uuid4

from core.extension_monitor import SUPPORTED_BROWSER_KEYS, build_extension_status
from core.time_utils import get_local_now


def _local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _local_naive(value)
    if not value:
        return None
    try:
        return _local_naive(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def _platform_map(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("key") or "").lower(): item
        for item in status.get("platforms", [])
        if str(item.get("key") or "").lower() in SUPPORTED_BROWSER_KEYS
    }


def _baseline_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    extension = status.get("extension", {})
    platforms = _platform_map(status)
    return {
        "last_heartbeat_at": extension.get("last_heartbeat_at"),
        "platforms": {
            key: {
                "events_total": int(item.get("events_total") or 0),
                "responses_total": int(item.get("responses_total") or 0),
                "content_script_last_seen_at": item.get(
                    "content_script_last_seen_at"
                ),
            }
            for key, item in platforms.items()
        },
    }


def evaluate_extension_verification(
    run: dict[str, Any],
    status: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """比較 start baseline 與現在狀態；歷史 event 不得冒充本輪 receipt。"""
    now = _local_naive(now)
    started_at = _local_naive(run["started_at"])
    deadline_at = _local_naive(run["deadline_at"])
    # Extension/API timestamp 以秒呈現，因此允許同秒內、但必須與 baseline 不同。
    started_floor = started_at.replace(microsecond=0)
    baseline = run["baseline"]
    extension = status.get("extension", {})
    current_heartbeat = _parse_timestamp(extension.get("last_heartbeat_at"))
    baseline_heartbeat = _parse_timestamp(baseline.get("last_heartbeat_at"))
    heartbeat_after_start = bool(
        extension.get("heartbeat_verified")
        and current_heartbeat is not None
        and current_heartbeat >= started_floor
        and current_heartbeat != baseline_heartbeat
    )

    current_platforms = _platform_map(status)
    platform_receipts = []
    next_actions: list[str] = []
    for key in run["required_platforms"]:
        current = current_platforms.get(key, {})
        previous = baseline.get("platforms", {}).get(key, {})
        events_total = int(current.get("events_total") or 0)
        responses_total = int(current.get("responses_total") or 0)
        event_delta = max(0, events_total - int(previous.get("events_total") or 0))
        response_delta = max(
            0,
            responses_total - int(previous.get("responses_total") or 0),
        )
        content_ready_at = _parse_timestamp(
            current.get("content_script_last_seen_at")
        )
        last_capture_at = _parse_timestamp(current.get("last_capture_at"))
        last_response_at = _parse_timestamp(current.get("last_response_at"))
        enabled = bool(current.get("enabled"))
        content_ready_after_start = bool(
            content_ready_at is not None and content_ready_at >= started_floor
        )
        event_after_start = bool(
            event_delta > 0
            and last_capture_at is not None
            and last_capture_at >= started_floor
        )
        response_after_start = bool(
            response_delta > 0
            and last_response_at is not None
            and last_response_at >= started_floor
        )
        passed = bool(
            enabled
            and content_ready_after_start
            and event_after_start
            and response_after_start
        )
        if not enabled:
            next_actions.append(f"enable_platform:{key}")
        elif not content_ready_after_start:
            next_actions.append(f"reload_target_tab:{key}")
        elif not event_after_start:
            next_actions.append(f"send_new_prompt:{key}")
        elif not response_after_start:
            next_actions.append(f"wait_for_assistant_response:{key}")
        platform_receipts.append(
            {
                "key": key,
                "enabled": enabled,
                "content_ready_after_start": content_ready_after_start,
                "content_script_last_seen_at": (
                    content_ready_at.isoformat(timespec="seconds")
                    if content_ready_at is not None
                    else None
                ),
                "event_after_start": event_after_start,
                "event_delta": event_delta,
                "response_after_start": response_after_start,
                "response_delta": response_delta,
                "last_capture_at": (
                    last_capture_at.isoformat(timespec="seconds")
                    if last_capture_at is not None
                    else None
                ),
                "last_response_at": (
                    last_response_at.isoformat(timespec="seconds")
                    if last_response_at is not None
                    else None
                ),
                "passed": passed,
            }
        )

    token_configured = bool(extension.get("token_configured"))
    if not token_configured:
        next_actions.insert(0, "configure_extension_token")
    elif not heartbeat_after_start:
        next_actions.insert(0, "open_extension_popup_or_reload")
    all_passed = bool(
        token_configured
        and heartbeat_after_start
        and platform_receipts
        and all(item["passed"] for item in platform_receipts)
    )
    expired = now >= deadline_at and not all_passed
    verification_status = "passed" if all_passed else "failed" if expired else "running"

    return {
        "verification_id": run["verification_id"],
        "status": verification_status,
        "started_at": started_at.isoformat(timespec="seconds"),
        "deadline_at": deadline_at.isoformat(timespec="seconds"),
        "remaining_seconds": max(0, int((deadline_at - now).total_seconds())),
        "required_platforms": list(run["required_platforms"]),
        "checks": {
            "token_configured": token_configured,
            "heartbeat_after_start": heartbeat_after_start,
            "last_heartbeat_at": (
                current_heartbeat.isoformat(timespec="seconds")
                if current_heartbeat is not None
                else None
            ),
            "platforms": platform_receipts,
        },
        "next_actions": list(dict.fromkeys(next_actions)),
        "persisted": False,
        "privacy_boundary": (
            "Receipt contains counts, timestamps, platform keys and stable state only; "
            "it excludes token, URL, prompt, response and local path."
        ),
        "claim_boundary": (
            "Passed proves a new token-authenticated heartbeat, content-ready receipt, "
            "browser event and non-empty response for each requested platform in this run. "
            "It does not prove continuous or complete coverage."
        ),
    }


class ExtensionVerificationRegistry:
    """保存少量 process-local verification baselines；重啟後明確失效。"""

    def __init__(
        self,
        *,
        status_provider: Callable[[], dict[str, Any]] = build_extension_status,
        now_provider: Callable[[], datetime] = get_local_now,
        max_runs: int = 32,
    ) -> None:
        self._status_provider = status_provider
        self._now_provider = now_provider
        self._max_runs = max(1, int(max_runs))
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _cleanup(self, now: datetime) -> None:
        stale_ids = [
            key
            for key, run in self._runs.items()
            if now > run["deadline_at"] + timedelta(hours=1)
        ]
        for key in stale_ids:
            self._runs.pop(key, None)
        while len(self._runs) >= self._max_runs:
            oldest = min(self._runs, key=lambda key: self._runs[key]["started_at"])
            self._runs.pop(oldest, None)

    def start(
        self,
        platforms: list[str],
        *,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        required = tuple(dict.fromkeys(str(item).strip().lower() for item in platforms))
        if not required or any(item not in SUPPORTED_BROWSER_KEYS for item in required):
            raise ValueError("invalid verification platforms")
        timeout_seconds = max(60, min(int(timeout_seconds), 1800))
        now = _local_naive(self._now_provider())
        current_status = self._status_provider()
        run = {
            "verification_id": uuid4().hex,
            "started_at": now,
            "deadline_at": now + timedelta(seconds=timeout_seconds),
            "required_platforms": required,
            "baseline": _baseline_snapshot(current_status),
        }
        with self._lock:
            self._cleanup(now)
            self._runs[run["verification_id"]] = run
        return evaluate_extension_verification(run, current_status, now=now)

    def get(self, verification_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(str(verification_id))
            if run is None:
                raise KeyError("verification run not found")
            run = dict(run)
        return evaluate_extension_verification(
            run,
            self._status_provider(),
            now=_local_naive(self._now_provider()),
        )


extension_verification_registry = ExtensionVerificationRegistry()
