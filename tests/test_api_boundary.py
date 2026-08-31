from fastapi.testclient import TestClient

from core.server import (
    app,
    browser_conversation_key,
    browser_response_status,
    get_system_config,
)
from core.secret_resolver import SecretResolution


client = TestClient(app)


def test_malicious_origin_cannot_read_config():
    response = client.get("/api/v1/config", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_config_response_redacts_secrets():
    response = client.get("/api/v1/config", headers={"Origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200
    payload = response.json()
    github_token = payload.get("integrations", {}).get("github", {}).get("token")
    browser_token = payload.get("security", {}).get("browser_extension_ingest_token")
    assert github_token in {"", "***REDACTED***"}
    assert browser_token in {"", "***REDACTED***"}


def test_empty_config_keeps_public_secret_field_contract(monkeypatch):
    class EmptyConfig:
        data = {}

    monkeypatch.setattr("core.server.get_config", lambda: EmptyConfig())
    payload = get_system_config()

    assert payload["integrations"]["github"]["token"] == ""
    assert payload["security"]["browser_extension_ingest_token"] == ""


def test_llm_status_exposes_source_but_never_secret(monkeypatch):
    monkeypatch.setattr(
        "core.server.resolve_secret_env",
        lambda name, aliases=(): SecretResolution(
            value="test-secret-never-returned",
            source="windows_user",
            env_var=name,
        ),
    )
    response = client.get(
        "/api/v1/llm/status",
        headers={"Origin": "http://127.0.0.1:8765"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"]["gemini"]["configured"] is True
    assert payload["providers"]["gemini"]["source"] == "windows_user"
    assert "test-secret-never-returned" not in response.text
    assert "value" not in payload["providers"]["gemini"]


def test_extension_ingest_without_token_is_denied():
    response = client.post(
        "/api/v1/events/ai",
        headers={"Origin": "chrome-extension://test-extension"},
        json={"platform": "chatgpt", "prompt_text": "test prompt"},
    )
    assert response.status_code == 403


def test_extension_status_pairing_probe_requires_token(monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_INGEST_TOKEN", "pairing-test-token")
    monkeypatch.setattr(
        "core.server.build_extension_status",
        lambda provided_token: {
            "extension": {
                "pairing_verified": provided_token == "pairing-test-token",
            }
        },
    )
    denied = client.get(
        "/api/v1/extension/status",
        headers={"Origin": "chrome-extension://test-extension"},
    )
    allowed = client.get(
        "/api/v1/extension/status",
        headers={
            "Origin": "chrome-extension://test-extension",
            "X-OmniContext-Ingest-Token": "pairing-test-token",
        },
    )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["extension"]["pairing_verified"] is True
    assert "pairing-test-token" not in allowed.text


def test_extension_heartbeat_requires_token_even_without_origin(monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_INGEST_TOKEN", "heartbeat-test-token")
    monkeypatch.setattr(
        "core.server.record_extension_heartbeat",
        lambda payload: {
            "status": "accepted",
            "server_received_at": "2026-08-25T10:00:00",
            "privacy_boundary": "no_token_no_url_no_prompt_no_response",
        },
    )
    body = {
        "instance_id": "test-extension-instance",
        "extension_version": "1.2.0",
        "ready_platforms": ["chatgpt"],
        "last_capture_status": "content_ready",
        "offline_queue_size": 0,
    }
    denied = client.post("/api/v1/extension/heartbeat", json=body)
    allowed = client.post(
        "/api/v1/extension/heartbeat",
        headers={
            "Origin": "chrome-extension://test-extension",
            "X-OmniContext-Ingest-Token": "heartbeat-test-token",
        },
        json=body,
    )
    assert denied.status_code == 401
    assert allowed.status_code == 202
    assert allowed.json()["status"] == "accepted"
    assert "heartbeat-test-token" not in allowed.text

    rejected_sensitive_extra = client.post(
        "/api/v1/extension/heartbeat",
        headers={"X-OmniContext-Ingest-Token": "heartbeat-test-token"},
        json={**body, "prompt_text": "heartbeat must not carry conversation content"},
    )
    assert rejected_sensitive_extra.status_code == 422


def test_extension_verification_api_is_local_only_and_forbids_extra_fields(monkeypatch):
    class FakeRegistry:
        def start(self, platforms, *, timeout_seconds):
            assert platforms == ["claude"]
            assert timeout_seconds == 300
            return {
                "verification_id": "verification-1",
                "status": "running",
                "persisted": False,
            }

        def get(self, verification_id):
            assert verification_id == "verification-1"
            return {
                "verification_id": verification_id,
                "status": "passed",
                "persisted": False,
            }

    monkeypatch.setattr("core.server.extension_verification_registry", FakeRegistry())
    started = client.post(
        "/api/v1/extension/verification",
        json={"platforms": ["claude"], "timeout_seconds": 300},
    )
    checked = client.get("/api/v1/extension/verification/verification-1")
    rejected_extra = client.post(
        "/api/v1/extension/verification",
        json={
            "platforms": ["claude"],
            "timeout_seconds": 300,
            "token": "must-not-be-accepted",
        },
    )
    hostile_origin = client.post(
        "/api/v1/extension/verification",
        headers={"Origin": "https://example.com"},
        json={"platforms": ["claude"], "timeout_seconds": 300},
    )

    assert started.status_code == 201
    assert checked.status_code == 200 and checked.json()["status"] == "passed"
    assert rejected_extra.status_code == 422
    assert hostile_origin.status_code == 403


def test_usage_api_exposes_claim_and_coverage_contract(monkeypatch):
    class FakeManager:
        def get_status(self):
            return {"collector_runtime": {}, "collector_health": {}}

    monkeypatch.setattr("core.server.get_manager", lambda: FakeManager())
    monkeypatch.setattr(
        "core.server.get_usage_summary",
        lambda *_args, **_kwargs: {
            "metric_label": "foreground_active_time",
            "claim_boundary": "not productivity",
            "coverage_status": "partial",
            "interfaces": [],
        },
    )
    response = client.get(
        "/api/v1/usage/today",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    assert response.json()["coverage_status"] == "partial"
    assert response.json()["metric_label"] == "foreground_active_time"


def test_background_task_api_keeps_duration_separate_and_content_private(monkeypatch):
    monkeypatch.setattr(
        "core.server.get_background_task_summary",
        lambda *_args, **_kwargs: {
            "metric_label": "verified_background_agent_execution_time",
            "claim_boundary": "not foreground or productivity",
            "verified_seconds": 600,
            "recent_tasks": [{"platform": "codex", "duration_seconds": 600}],
        },
    )
    response = client.get(
        "/api/v1/background-tasks/today",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    assert response.json()["metric_label"] == "verified_background_agent_execution_time"
    assert "prompt_text" not in response.text
    assert "response_text" not in response.text
    assert "source_path" not in response.text


def test_capture_status_api_exposes_separate_channels_without_sensitive_content(monkeypatch):
    monkeypatch.setattr(
        "core.server.build_capture_coverage",
        lambda: {
            "platforms": [
                {
                    "key": "claude",
                    "desktop_focus": {"state": "observed"},
                    "web_capture": {"state": "waiting"},
                    "transcript_capture": {"state": "observed"},
                }
            ],
            "claim_boundary": "separate channels",
        },
    )
    response = client.get(
        "/api/v1/capture/status",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["platforms"][0]["desktop_focus"]["state"] == "observed"
    assert payload["platforms"][0]["web_capture"]["state"] == "waiting"
    assert "prompt_text" not in response.text
    assert "source_path" not in response.text


def test_context_sessions_api_keeps_temporal_inference_boundary(monkeypatch):
    monkeypatch.setattr(
        "core.server.build_recent_work_sessions",
        lambda **_kwargs: {
            "status": "observed",
            "sessions": [{"session_id": "abc", "inference_status": "temporal_grouping"}],
            "claim_boundary": "not productivity or task continuity",
        },
    )
    response = client.get(
        "/api/v1/context/sessions?hours=24&limit=4",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    assert response.json()["sessions"][0]["inference_status"] == "temporal_grouping"
    assert "not productivity" in response.json()["claim_boundary"]


def test_related_context_api_is_local_advisory_and_rejects_extra_fields(monkeypatch):
    monkeypatch.setattr(
        "core.server.find_related_work",
        lambda question, **_kwargs: {
            "status": "related_history_found",
            "question": question,
            "query_persisted": False,
            "matches": [{"source_ref": "ai_prompt_events:7", "score": 0.91}],
            "claim_boundary": "similarity is not truth",
        },
    )
    response = client.post(
        "/api/v1/context/related",
        headers={"Origin": "http://127.0.0.1:8765"},
        json={"question": "rollback rehearsal", "threshold": 0.8},
    )
    rejected = client.post(
        "/api/v1/context/related",
        headers={"Origin": "http://127.0.0.1:8765"},
        json={"question": "rollback rehearsal", "secret": "must-not-be-accepted"},
    )
    assert response.status_code == 200
    assert response.json()["query_persisted"] is False
    assert response.json()["matches"][0]["source_ref"] == "ai_prompt_events:7"
    assert rejected.status_code == 422


def test_secretary_proposals_api_is_read_only_and_origin_protected(monkeypatch):
    monkeypatch.setattr(
        "core.server.build_action_proposals",
        lambda **_kwargs: {
            "status": "proposal_only",
            "mode": "proposal_only",
            "execution_available": False,
            "cloud_llm_used": False,
            "query_persisted": False,
            "proposals": [
                {
                    "proposal_id": "stable-proposal-id",
                    "evidence_refs": ["open_loops:7"],
                    "execution_available": False,
                }
            ],
        },
    )
    allowed = client.get(
        "/api/v1/secretary/proposals?limit=4",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    denied = client.get(
        "/api/v1/secretary/proposals",
        headers={"Origin": "https://evil.example"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["execution_available"] is False
    assert allowed.json()["proposals"][0]["evidence_refs"] == ["open_loops:7"]
    assert denied.status_code == 403


def test_localhost_monitor_page_is_dashboard_native_not_extension_storage():
    monitor = client.get(
        "/extension-monitor",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    dashboard = client.get(
        "/",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert monitor.status_code == 200
    assert "OmniContext Extension Monitor" in monitor.text
    assert "尚未驗證 Extension" in monitor.text
    assert "RECENT HEARTBEAT" in monitor.text
    assert "CONTENT READY" in monitor.text
    assert "LIVE VERIFICATION" in monitor.text
    assert "start-verification" in monitor.text
    assert "/api/v1/extension/verification" in monitor.text
    assert "chrome.storage" not in monitor.text
    assert "/api/v1/extension/status" in monitor.text
    assert dashboard.status_code == 200
    assert "usage-goal-value" in dashboard.text
    assert "background-tasks-value" in dashboard.text
    assert "background-tasks-list" in dashboard.text
    assert "capture-coverage-list" in dashboard.text
    assert "llm-key-status-badge" in dashboard.text
    assert "context-sessions-list" in dashboard.text
    assert "input-related-question" in dashboard.text
    assert "secretary-inbox" in dashboard.text
    assert "secretary-proposals-list" in dashboard.text
    assert "SECRETARY SUGGESTIONS" in dashboard.text
    assert "PROPOSAL ONLY" in dashboard.text
    assert "tab-assistant" in dashboard.text
    assert "input-assistant-prompt" in dashboard.text
    assert "assistant-chat-messages" in dashboard.text
    assert "toggle-executor-enabled" in dashboard.text
    assert "toggle-executor-l2" in dashboard.text
    assert "select-agent-cli" in dashboard.text
    assert "DATA CAPTURE" in dashboard.text
    assert "extension-capture-badge" not in dashboard.text
    assert "style.css?v=1.3.0a12-home-work-guide" in dashboard.text
    assert "app.js?v=1.3.0a12-home-work-guide" in dashboard.text
    assert "focus-carousel" in dashboard.text
    assert "repo-sync-panel" in dashboard.text
    assert "data-trust-runtime-badge" in dashboard.text
    assert "/extension-monitor" in dashboard.text
    stylesheet = client.get("/static/style.css")
    assert stylesheet.status_code == 200
    assert 'input:not([type="checkbox"])' in stylesheet.text
    assert ".usage-toggle-row" in stylesheet.text
    assert ".background-task-body" in stylesheet.text
    assert ".pill-warn" in stylesheet.text
    assert ".collector-diagnostic" in stylesheet.text
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in stylesheet.text
    assert ".split { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }" in stylesheet.text
    assert ".split-col { min-width: 0;" in stylesheet.text


def test_checkpoint_path_traversal_is_denied():
    response = client.get(
        "/api/v1/logs/checkpoints/..%2F..%2Fconfig.yaml",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code in {400, 404}


def test_browser_turn_contract_distinguishes_conversations_and_partial_state():
    first = browser_conversation_key(None, "https://chatgpt.com/c/one")
    second = browser_conversation_key(None, "https://chatgpt.com/c/two")
    assert first != second
    assert browser_response_status("streaming text", "partial_timeout") == "partial"
    assert browser_response_status("stable text", "stable_candidate") == "final_candidate"
    assert browser_response_status(None, "stable_candidate") == "missing"
