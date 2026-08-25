from core.secret_resolver import SecretResolution, resolve_secret_env


def test_process_environment_has_priority(monkeypatch):
    monkeypatch.setenv("TEST_OMNI_KEY", "process-secret")
    monkeypatch.setattr(
        "core.secret_resolver._read_windows_registry_env",
        lambda name: ("registry-secret", "windows_user"),
    )

    result = resolve_secret_env("TEST_OMNI_KEY")

    assert result.value == "process-secret"
    assert result.source == "process"


def test_windows_environment_fallback_handles_stale_parent_process(monkeypatch):
    monkeypatch.delenv("TEST_OMNI_KEY", raising=False)
    monkeypatch.setattr(
        "core.secret_resolver._read_windows_registry_env",
        lambda name: ("registry-secret", "windows_user"),
    )

    result = resolve_secret_env("TEST_OMNI_KEY")

    assert result.configured is True
    assert result.source == "windows_user"
    assert result.env_var == "TEST_OMNI_KEY"


def test_public_status_never_contains_secret_value():
    status = SecretResolution(
        value="must-not-leak",
        source="windows_machine",
        env_var="TEST_OMNI_KEY",
    ).public_status()

    assert status == {
        "configured": True,
        "source": "windows_machine",
        "env_var": "TEST_OMNI_KEY",
    }
    assert "must-not-leak" not in str(status)


def test_invalid_environment_variable_name_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "core.secret_resolver._read_windows_registry_env",
        lambda name: (_ for _ in ()).throw(AssertionError("must not query registry")),
    )

    result = resolve_secret_env(r"..\Environment\GEMINI_API_KEY")

    assert result.configured is False
    assert result.env_var == ""
