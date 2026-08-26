from datetime import datetime
import json

import pytest

from core.extension_verification import ExtensionVerificationRegistry


def _status(
    *,
    heartbeat_at: str,
    heartbeat_verified: bool,
    claude_events: int = 2,
    claude_responses: int = 1,
    manus_events: int = 1,
    manus_responses: int = 0,
    ready_at: str | None = None,
):
    def platform(key, events, responses):
        return {
            "key": key,
            "enabled": True,
            "events_total": events,
            "responses_total": responses,
            "last_capture_at": heartbeat_at,
            "last_response_at": heartbeat_at if responses else None,
            "content_script_last_seen_at": ready_at,
        }

    return {
        "extension": {
            "token_configured": True,
            "heartbeat_verified": heartbeat_verified,
            "last_heartbeat_at": heartbeat_at,
        },
        "platforms": [
            platform("claude", claude_events, claude_responses),
            platform("manus", manus_events, manus_responses),
        ],
    }


def test_live_verification_requires_new_heartbeat_content_event_and_response():
    clock = [datetime(2026, 8, 26, 10, 0, 10)]
    statuses = [
        _status(
            heartbeat_at="2026-08-26T09:59:00",
            heartbeat_verified=True,
            ready_at="2026-08-26T09:58:00",
        )
    ]
    registry = ExtensionVerificationRegistry(
        status_provider=lambda: statuses[-1],
        now_provider=lambda: clock[-1],
    )

    started = registry.start(["claude", "manus"], timeout_seconds=600)
    assert started["status"] == "running"
    assert started["checks"]["heartbeat_after_start"] is False

    clock.append(datetime(2026, 8, 26, 10, 1, 0))
    statuses.append(
        _status(
            heartbeat_at="2026-08-26T10:00:50",
            heartbeat_verified=True,
            claude_events=3,
            claude_responses=2,
            manus_events=2,
            manus_responses=1,
            ready_at="2026-08-26T10:00:30",
        )
    )
    receipt = registry.get(started["verification_id"])

    assert receipt["status"] == "passed"
    assert receipt["checks"]["heartbeat_after_start"] is True
    assert all(item["passed"] for item in receipt["checks"]["platforms"])
    assert receipt["persisted"] is False
    serialized = json.dumps(receipt)
    for forbidden_key in ("prompt_text", "response_text", "ingest_token", "url", "local_path"):
        assert forbidden_key not in serialized


def test_historical_observation_cannot_pass_and_expiry_is_fail_closed():
    clock = [datetime(2026, 8, 26, 10, 0, 0)]
    statuses = [
        _status(
            heartbeat_at="2026-08-26T09:59:00",
            heartbeat_verified=True,
            ready_at="2026-08-26T09:58:00",
        )
    ]
    registry = ExtensionVerificationRegistry(
        status_provider=lambda: statuses[-1],
        now_provider=lambda: clock[-1],
    )
    started = registry.start(["claude"], timeout_seconds=60)

    clock.append(datetime(2026, 8, 26, 10, 1, 1))
    receipt = registry.get(started["verification_id"])

    assert receipt["status"] == "failed"
    assert receipt["checks"]["heartbeat_after_start"] is False
    assert receipt["checks"]["platforms"][0]["event_delta"] == 0
    assert "open_extension_popup_or_reload" in receipt["next_actions"]
    assert "reload_target_tab:claude" in receipt["next_actions"]


def test_verification_rejects_empty_or_unknown_platforms():
    registry = ExtensionVerificationRegistry(
        status_provider=lambda: _status(
            heartbeat_at="2026-08-26T10:00:00",
            heartbeat_verified=False,
        )
    )
    with pytest.raises(ValueError):
        registry.start([])
    with pytest.raises(ValueError):
        registry.start(["unknown"])
