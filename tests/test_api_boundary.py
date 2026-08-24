from fastapi.testclient import TestClient

from core.server import (
    app,
    browser_conversation_key,
    browser_response_status,
    get_system_config,
)


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
    assert "chrome.storage" not in monitor.text
    assert "/api/v1/extension/status" in monitor.text
    assert dashboard.status_code == 200
    assert "usage-goal-value" in dashboard.text
    assert "/extension-monitor" in dashboard.text


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
