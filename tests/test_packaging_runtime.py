try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 使用相容套件。
    import tomli as tomllib
from pathlib import Path

import yaml

import main
from core.runtime_paths import (
    application_home,
    default_config_path,
    resolve_runtime_path,
    runtime_asset_status,
)


def test_application_home_override_routes_relative_runtime_data(tmp_path, monkeypatch):
    app_home = tmp_path / "portable-home"
    monkeypatch.setenv("OMNICONTEXT_HOME", str(app_home))
    monkeypatch.delenv("OMNICONTEXT_CONFIG", raising=False)

    assert application_home() == app_home.resolve()
    assert default_config_path() == (app_home / "config.yaml").resolve()
    assert resolve_runtime_path("reports") == (app_home / "reports").resolve()


def test_explicit_config_routes_relative_data_to_config_parent(tmp_path, monkeypatch):
    config_path = tmp_path / "profile" / "custom.yaml"
    monkeypatch.delenv("OMNICONTEXT_HOME", raising=False)
    monkeypatch.setenv("OMNICONTEXT_CONFIG", str(config_path))

    assert default_config_path() == config_path.resolve()
    assert resolve_runtime_path("omni_context.db") == (
        config_path.parent / "omni_context.db"
    ).resolve()


def test_source_runtime_assets_are_complete():
    status = runtime_asset_status()
    assert status["status"] == "ok"
    assert all(status["checks"].values())


def test_init_uses_writable_application_home(tmp_path, monkeypatch):
    app_home = tmp_path / "installed-home"
    watched = tmp_path / "watched"
    watched.mkdir()
    monkeypatch.setenv("OMNICONTEXT_HOME", str(app_home))
    monkeypatch.delenv("OMNICONTEXT_CONFIG", raising=False)

    class FakeConfig:
        loaded_path = None

        def load(self, path):
            self.loaded_path = Path(path)

    fake_config = FakeConfig()
    monkeypatch.setattr(main, "get_config", lambda: fake_config)

    main.cmd_init([str(watched)])

    config_path = app_home / "config.yaml"
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert fake_config.loaded_path == config_path.resolve()
    assert config_data["watchers"]["file_watcher"]["watch_directories"] == [
        str(watched.resolve())
    ]
    assert config_data["security"]["browser_extension_ingest_token"]
    assert (app_home / "reports").is_dir()
    assert (app_home / "logs" / "checkpoints").is_dir()


def test_pyproject_declares_wheel_runtime_assets():
    project_root = Path(__file__).resolve().parent.parent
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = metadata["tool"]["setuptools"]

    assert setuptools["data-files"]["share/omnicontext"] == ["config.example.yaml"]
    assert "*.html" in setuptools["package-data"]["web"]
    assert "browser_extension/content_scripts/*.js" in setuptools["package-data"]["watchers"]
