"""本機 Git 同步中心：只對明示設定 root 下的 repo 作用。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.repo_sync import LocalRepositorySync, RepositoryReference, RepositorySyncRejected
from core.server import app


class _Config:
    def __init__(self, root: Path):
        self.root = root

    def get_paths(self, key: str):
        return [self.root] if key == "watchers.git_watcher.repositories" else []

    def get(self, key: str, default=None):
        values = {
            "watchers.git_watcher.max_depth": 2,
            "repository_sync.command_timeout_seconds": 20,
            "repository_sync.max_repositories": 20,
            "repository_sync.dashboard_recent_limit": 10,
        }
        return values.get(key, default)


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


@pytest.fixture()
def synced_repo(tmp_path: Path) -> Path:
    root = tmp_path / "roots"
    repo = root / "sample"
    remote = tmp_path / "remote.git"
    repo.mkdir(parents=True)
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "OmniContext test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main")
    return root


def _one_status(root: Path) -> tuple[LocalRepositorySync, dict]:
    service = LocalRepositorySync(_Config(root))
    payload = service.list_statuses()
    assert payload["repository_count"] == 1
    return service, payload["repositories"][0]


def test_status_uses_only_configured_root_and_cached_remote_tracking_ref(synced_repo: Path):
    service, status = _one_status(synced_repo)

    assert status["name"] == "sample"
    assert status["sync_state"] == "synced"
    assert status["remote_tracking_basis"] == "cached_local_remote_tracking_ref"
    assert status["clean"] is True
    assert status["actions"]["pull_ff_only"]["allowed"] is False
    with pytest.raises(RepositorySyncRejected, match="找不到"):
        service.execute("0" * 16, "fetch")


def test_staged_only_commit_never_auto_adds_other_files(synced_repo: Path):
    repo = synced_repo / "sample"
    (repo / "README.md").write_text("staged\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("must remain untracked\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    service, status = _one_status(synced_repo)

    assert status["worktree"]["staged_files"] == 1
    assert status["worktree"]["untracked_files"] == 1
    assert status["actions"]["commit_staged"]["allowed"] is True
    receipt = service.execute(status["repo_id"], "commit_staged", "commit staged only")

    assert receipt["status"] == "success"
    porcelain = _git(repo, "status", "--porcelain").stdout
    assert "?? untracked.txt" in porcelain
    assert "README.md" not in porcelain


def test_push_is_blocked_when_worktree_is_not_clean(synced_repo: Path):
    repo = synced_repo / "sample"
    (repo / "README.md").write_text("ahead\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "ahead")
    (repo / "local-note.txt").write_text("dirty\n", encoding="utf-8")
    service, status = _one_status(synced_repo)

    assert status["sync_state"] == "ahead"
    assert status["actions"]["push"]["allowed"] is False
    with pytest.raises(RepositorySyncRejected, match="clean worktree"):
        service.execute(status["repo_id"], "push")


def test_api_uses_repo_id_and_rejects_arbitrary_path_field(synced_repo: Path, monkeypatch):
    service, status = _one_status(synced_repo)
    monkeypatch.setattr("core.server.LocalRepositorySync", lambda: service)
    client = TestClient(app)

    listed = client.get("/api/v1/repos/sync-status")
    assert listed.status_code == 200
    assert listed.json()["repositories"][0]["repo_id"] == status["repo_id"]

    rejected = client.post("/api/v1/repos/sync-action", json={
        "repo_id": status["repo_id"],
        "action": "fetch",
        "confirmation": "confirmed",
        "path": "C:/not-allowed",
    })
    assert rejected.status_code == 422


def test_status_returns_only_recent_limit_in_descending_activity_order(monkeypatch, tmp_path: Path):
    root = tmp_path / "roots"
    root.mkdir()
    service = LocalRepositorySync(_Config(root))
    references = [
        RepositoryReference(f"{index:016x}", root / f"repo-{index:02d}")
        for index in range(12)
    ]
    monkeypatch.setattr(service, "_discover_references", lambda: (references, False))
    monkeypatch.setattr(service, "_last_commit_epoch", lambda repo: int(repo.path.name[-2:]))
    monkeypatch.setattr(
        service,
        "_status_for",
        lambda repo: {
            "repo_id": repo.repo_id,
            "name": repo.path.name,
            "path": str(repo.path),
            "last_activity_at": f"2026-08-29T{int(repo.path.name[-2:]):02d}:00:00+08:00",
            "last_activity_source": "local_commit",
            "_sort_last_activity_epoch": int(repo.path.name[-2:]),
            "actions": {},
        },
    )

    payload = service.list_statuses()

    assert payload["repository_count"] == 12
    assert payload["displayed_count"] == 10
    assert payload["attention_scope"] == "displayed_repositories"
    assert [item["name"] for item in payload["repositories"]] == [
        f"repo-{index:02d}" for index in range(11, 1, -1)
    ]
