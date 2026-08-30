from scripts.background_task_live_acceptance import (
    evaluate_background_receipts,
    render_status_snippet,
)
from scripts.extension_live_acceptance import (
    describe_next_actions,
    summarize_verification,
)


def _background_summary():
    return {
        "date": "2026-08-30",
        "verified_seconds": 3076.659,
        "completed_task_count": 8,
        "awaiting_final_count": 2,
        "untrusted_duration_count": 1,
        "claim_boundary": "Only paired local agent task receipts ...",
        "interfaces": [
            {"platform": "codex", "verified_seconds": 2000.0, "completed_tasks": 7},
            {"platform": "claude_code", "verified_seconds": 1100.5, "completed_tasks": 1},
        ],
    }


def test_background_acceptance_passes_only_when_every_platform_has_receipt():
    passed = evaluate_background_receipts(
        _background_summary(), ["claude_code", "codex"]
    )
    assert passed["status"] == "passed"
    assert all(item["passed"] for item in passed["platforms"])

    failed = evaluate_background_receipts(
        _background_summary(), ["claude_code", "claude_desktop", "codex"]
    )
    assert failed["status"] == "failed"
    missing = {item["platform"]: item for item in failed["platforms"]}
    assert missing["claude_desktop"]["passed"] is False
    assert missing["claude_desktop"]["completed_tasks_today"] == 0
    # 已通過的平台仍逐平台回報，不因整體失敗而遺失。
    assert missing["codex"]["passed"] is True


def test_background_acceptance_requires_at_least_one_platform():
    receipt = evaluate_background_receipts(_background_summary(), [])
    assert receipt["status"] == "failed"


def test_status_snippet_lists_each_platform_receipt():
    receipt = evaluate_background_receipts(_background_summary(), ["codex"])
    receipt["captured_at"] = "2026-08-30T21:00:00+08:00"
    snippet = render_status_snippet(receipt)
    assert "codex_completed_receipts_today: 7" in snippet
    assert "codex_verified_seconds_today: 2000.0" in snippet
    assert "claim_boundary" in snippet


def test_next_action_hints_cover_platform_scoped_codes_and_passthrough():
    steps = describe_next_actions(
        [
            "configure_extension_token",
            "open_extension_popup_or_reload",
            "enable_platform:claude",
            "reload_target_tab:chatgpt",
            "send_new_prompt:chatgpt",
            "wait_for_assistant_response:claude",
            "unknown_future_action",
        ]
    )
    assert any("token" in step for step in steps)
    assert any("claude" in step and "啟用" in step for step in steps)
    assert any("chatgpt" in step and "重新整理" in step for step in steps)
    assert any("chatgpt" in step and "提問" in step for step in steps)
    assert steps[-1] == "unknown_future_action"


def test_summarize_verification_keeps_only_non_sensitive_fields():
    payload = {
        "verification_id": "abc123",
        "status": "passed",
        "started_at": "2026-08-30T20:00:00",
        "deadline_at": "2026-08-30T20:10:00",
        "required_platforms": ["chatgpt", "claude"],
        "next_actions": [],
        "privacy_boundary": "no token/url/prompt/response",
        "claim_boundary": "this run only",
        "checks": {
            "token_configured": True,
            "heartbeat_after_start": True,
            "last_heartbeat_at": "2026-08-30T20:05:00",
            "platforms": [
                {
                    "key": "chatgpt",
                    "enabled": True,
                    "content_ready_after_start": True,
                    "content_script_last_seen_at": "2026-08-30T20:04:00",
                    "event_after_start": True,
                    "event_delta": 2,
                    "response_after_start": True,
                    "response_delta": 1,
                    "last_capture_at": "2026-08-30T20:05:30",
                    "last_response_at": "2026-08-30T20:06:00",
                    "passed": True,
                }
            ],
        },
    }
    receipt = summarize_verification(payload)
    assert receipt["status"] == "passed"
    assert receipt["token_configured"] is True
    assert receipt["heartbeat_after_start"] is True
    assert receipt["platforms"][0]["key"] == "chatgpt"
    assert receipt["platforms"][0]["event_delta"] == 2
    assert receipt["platforms"][0]["passed"] is True
    assert "checks" not in receipt
