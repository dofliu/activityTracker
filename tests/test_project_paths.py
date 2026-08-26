from pathlib import Path

from core.project_paths import (
    configured_project_search_roots,
    configured_self_project_path,
    find_configured_project_path,
)


class StubConfig:
    def __init__(self, paths: dict[str, list[Path]], self_path: Path | None = None):
        self.paths = paths
        self.self_path = self_path

    def get_paths(self, key_path: str) -> list[Path]:
        return self.paths.get(key_path, [])

    def get_path(self, key_path: str, default="") -> Path:
        if key_path == "project_resolution.self_project_path" and self.self_path:
            return self.self_path
        return Path(default)


def test_project_search_uses_explicit_roots_in_order_and_deduplicates(tmp_path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    cfg = StubConfig({"project_resolution.search_roots": [primary, primary, secondary]})

    assert configured_project_search_roots(cfg) == (primary.resolve(), secondary.resolve())


def test_project_search_falls_back_to_configured_watchers_and_finds_project(tmp_path):
    watched = tmp_path / "watched"
    repository = tmp_path / "repositories"
    target = repository / "portable-demo"
    watched.mkdir()
    target.mkdir(parents=True)
    cfg = StubConfig(
        {
            "watchers.file_watcher.watch_directories": [watched],
            "watchers.git_watcher.repositories": [repository],
        }
    )

    assert configured_project_search_roots(cfg) == (watched.resolve(), repository.resolve())
    assert find_configured_project_path("portable-demo", cfg) == target.resolve()


def test_self_project_path_requires_existing_configured_directory(tmp_path):
    configured = tmp_path / "omnicontext"
    configured.mkdir()

    assert configured_self_project_path(StubConfig({}, configured)) == configured.resolve()
    assert configured_self_project_path(StubConfig({}, tmp_path / "missing")) is None
