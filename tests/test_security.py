from core.security import (
    REDACTED,
    configured_allowed_origins,
    extension_ingest_authorized,
    merge_redacted_config,
    origin_is_allowed,
    redact_config,
)


class DictConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        current = self.values
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current


def test_config_redaction_and_merge_preserve_secret():
    original = {
        "integrations": {"github": {"token": "secret", "token_env": "GITHUB_TOKEN"}},
        "security": {"browser_extension_ingest_token": "browser-secret"},
        "server": {"port": 8765},
    }
    redacted = redact_config(original)
    assert redacted["integrations"]["github"]["token"] == REDACTED
    assert redacted["integrations"]["github"]["token_env"] == "GITHUB_TOKEN"
    assert redacted["security"]["browser_extension_ingest_token"] == REDACTED
    merged = merge_redacted_config(original, redacted)
    assert merged == original


def test_origin_allowlist_rejects_wildcard_and_unknown_origin():
    cfg = DictConfig({"security": {"allowed_origins": ["*", "http://127.0.0.1:8765/"]}})
    allowed = configured_allowed_origins(cfg)
    assert allowed == ["http://127.0.0.1:8765"]
    assert origin_is_allowed("http://127.0.0.1:8765", allowed)
    assert not origin_is_allowed("https://attacker.example", allowed)


def test_extension_token_fails_closed(monkeypatch):
    cfg = DictConfig({"security": {"browser_extension_ingest_token_env": "TEST_OMNI_TOKEN"}})
    monkeypatch.delenv("TEST_OMNI_TOKEN", raising=False)
    assert not extension_ingest_authorized(None, cfg)
    assert not extension_ingest_authorized("anything", cfg)
    monkeypatch.setenv("TEST_OMNI_TOKEN", "expected")
    assert extension_ingest_authorized("expected", cfg)
    assert not extension_ingest_authorized("wrong", cfg)
