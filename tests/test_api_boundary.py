from fastapi.testclient import TestClient

from core.server import app, browser_conversation_key, browser_response_status


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
    assert "Extension token 配對成功" in monitor.text
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
