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


def test_push_is_blocked_by_uncommitted_tracked_changes(synced_repo: Path):
    """真正該擋的是「已追蹤檔案有未提交的變更」——理由要說出實際數量。"""
    repo = synced_repo / "sample"
    (repo / "README.md").write_text("ahead\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "ahead")
    (repo / "README.md").write_text("edited but not committed\n", encoding="utf-8")
    service, status = _one_status(synced_repo)

    assert status["sync_state"] == "ahead"
    assert status["tracked_clean"] is False
    assert status["actions"]["push"]["allowed"] is False
    assert "unstaged 1" in status["actions"]["push"]["reason"]
    with pytest.raises(RepositorySyncRejected, match="未提交的變更"):
        service.execute(status["repo_id"], "push")


def test_untracked_files_do_not_block_push(synced_repo: Path):
    """.lock、build 產物之類的 untracked 檔案與 push 無關（ADR-011 Addendum C）。"""
    repo = synced_repo / "sample"
    (repo / "README.md").write_text("ahead\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "ahead")
    (repo / "project.lock").write_text("lock\n", encoding="utf-8")
    service, status = _one_status(synced_repo)

    assert status["sync_state"] == "ahead"
    assert status["clean"] is False            # 整體 worktree 確實不是空的
    assert status["tracked_clean"] is True     # 但沒有未提交的已追蹤變更
    assert status["actions"]["push"]["allowed"] is True
    receipt = service.execute(status["repo_id"], "push")
    assert receipt["status"] == "success"
    assert (repo / "project.lock").exists()    # 動作不碰使用者的本機檔案


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


@pytest.fixture()
def behind_repo(synced_repo: Path) -> Path:
    """讓本機落後遠端一個 commit（透過第二個 clone 推上去），並更新 remote-tracking ref。"""
    repo = synced_repo / "sample"
    remote = repo.parent.parent / "remote.git"
    other = repo.parent.parent / "other"
    # bare remote 的 HEAD 預設指向 master，不指定 -b 會 clone 出沒有 checkout 的樹。
    subprocess.run(["git", "clone", "-b", "main", str(remote), str(other)], check=True, text=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(other, "config", "user.name", "OmniContext test")
    _git(other, "config", "user.email", "test@example.invalid")
    (other / "upstream-only.txt").write_text("from remote\n", encoding="utf-8")
    _git(other, "add", "upstream-only.txt")
    _git(other, "commit", "-m", "remote advance")
    _git(other, "push", "origin", "main")
    _git(repo, "fetch", "origin")
    return synced_repo


def test_untracked_lock_file_does_not_block_pull(behind_repo: Path):
    """使用者實測回報的情境：repo 明明落後遠端，卻因為一個 .lock 檔而不給 pull。

    Git 自己對 fast-forward 只在「untracked 檔案會被覆蓋」時才拒絕（且會保留本機
    內容），所以多擋一層只會讓幾乎每個真實專案都 pull 不了。
    """
    repo = behind_repo / "sample"
    (repo / "uv.lock").write_text("lock\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_text("artifact\n", encoding="utf-8")
    service, status = _one_status(behind_repo)

    assert status["sync_state"] == "behind" and status["behind"] == 1
    assert status["clean"] is False and status["tracked_clean"] is True
    assert status["actions"]["pull_ff_only"]["allowed"] is True
    receipt = service.execute(status["repo_id"], "pull_ff_only")
    assert receipt["status"] == "success"
    assert (repo / "upstream-only.txt").exists()   # 遠端的 commit 真的進來了
    assert (repo / "uv.lock").read_text(encoding="utf-8") == "lock\n"   # 本機檔案原封不動


def test_pull_blocked_reason_names_the_actual_uncommitted_counts(behind_repo: Path):
    repo = behind_repo / "sample"
    (repo / "README.md").write_text("local edit\n", encoding="utf-8")
    _, status = _one_status(behind_repo)

    reason = status["actions"]["pull_ff_only"]["reason"]
    assert status["actions"]["pull_ff_only"]["allowed"] is False
    assert "unstaged 1" in reason and "untracked 檔案不影響" in reason


def test_pull_blocked_reason_names_divergence_with_numbers(behind_repo: Path):
    repo = behind_repo / "sample"
    (repo / "local.txt").write_text("mine\n", encoding="utf-8")
    _git(repo, "add", "local.txt")
    _git(repo, "commit", "-m", "local commit")
    _, status = _one_status(behind_repo)

    assert status["sync_state"] == "diverged"
    assert "領先 1" in status["actions"]["pull_ff_only"]["reason"]
    assert "落後 1" in status["actions"]["pull_ff_only"]["reason"]


def test_up_to_date_repo_says_so_instead_of_a_generic_condition(synced_repo: Path):
    _, status = _one_status(synced_repo)
    reason = status["actions"]["pull_ff_only"]["reason"]

    assert status["sync_state"] == "synced"
    assert "沒有落後" in reason
    assert "fetch" in reason  # 附上上次 fetch 時間，才知道這個判斷有多新


def test_branch_without_upstream_says_upstream_is_missing(synced_repo: Path):
    repo = synced_repo / "sample"
    _git(repo, "checkout", "-b", "feature")
    _, status = _one_status(synced_repo)

    assert status["sync_state"] == "no_upstream"
    reason = status["actions"]["pull_ff_only"]["reason"]
    assert "feature" in reason and "git push -u" in reason


def test_untracked_only_repo_is_not_counted_as_needing_attention(synced_repo: Path):
    (synced_repo / "sample" / "scratch.tmp").write_text("x\n", encoding="utf-8")
    payload = LocalRepositorySync(_Config(synced_repo)).list_statuses()

    assert payload["summary"]["dirty"] == 0
    assert payload["attention_count"] == 0
