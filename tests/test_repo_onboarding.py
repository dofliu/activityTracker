"""P4.3 Repo Onboarding／Reconciliation 的 contract tests（FEATURE-009）。

逐條驗證 trust boundary：不得由同名自動配對（只認 remote URL）、不得
自動初始化或發布（永不 push）、不得覆寫非空目錄、不得批次 create/clone
（schema 單一目標）、不得 force、不接受 dashboard 傳入任意路徑（id-based）。
全部使用真實 tmp git repo；clone 來源以 monkeypatch 指向本機 bare repo。
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, GitHubRepoState
from core.repo_onboarding import (
    RepoOnboarding,
    RepoOnboardingRejected,
    canonical_github_slug,
)
from core.repo_sync import _repository_id
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
            "repository_onboarding.clone_timeout_seconds": 60,
        }
        return values.get(key, default)


class TempDatabase:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=path, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _make_repo(path: Path, *, remote_url: str | None = None) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.name", "OmniContext test")
    _git(path, "config", "user.email", "test@example.invalid")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "seed")
    if remote_url:
        _git(path, "remote", "add", "origin", remote_url)
    return path


def _seed_github(database: TempDatabase, full_name: str, *, private=False) -> None:
    owner, name = full_name.split("/", 1)
    with database.session_scope() as session:
        session.add(
            GitHubRepoState(
                repo_name=name,
                full_name=full_name,
                is_private=private,
                html_url=f"https://github.com/{full_name}",
            )
        )


@pytest.fixture()
def workspace(tmp_path: Path):
    """root 下：cloned/（有 origin 指向 GitHub）、orphan/（無 remote）、
    plain/（未 git init）；DB 內三個 GitHub repo，其中一個與 cloned 對應、
    一個與 orphan 同名（不得自動配對）、一個完全無本機對應。"""
    root = tmp_path / "roots"
    root.mkdir()
    _make_repo(root / "cloned", remote_url="git@github.com:dofliu/cloned.git")
    _make_repo(root / "orphan")
    (root / "plain").mkdir()
    (root / "plain" / "note.txt").write_text("x", encoding="utf-8")
    database = TempDatabase()
    _seed_github(database, "dofliu/cloned")
    _seed_github(database, "dofliu/orphan")  # 與本機 orphan 同名，但無 remote 關聯
    _seed_github(database, "dofliu/cloudOnly", private=True)
    service = RepoOnboarding(cfg=_Config(root), database=database)
    return root, database, service


# ---- URL 正規化與配對 ----


def test_canonical_github_slug_variants():
    expected = "dofliu/activitytracker"
    for url in (
        "https://github.com/dofliu/activityTracker",
        "https://github.com/dofliu/activityTracker.git",
        "https://github.com/dofliu/activityTracker/",
        "git@github.com:dofliu/activityTracker.git",
        "ssh://git@github.com/dofliu/activityTracker",
        "https://user@github.com/dofliu/activityTracker.git",
    ):
        assert canonical_github_slug(url) == expected
    assert canonical_github_slug("https://gitlab.com/x/y") is None
    assert canonical_github_slug("") is None
    assert canonical_github_slug(None) is None


def test_report_classifies_three_scenarios_and_never_pairs_by_name(workspace):
    root, _db, service = workspace
    report = service.build_report()

    assert [item["name"] for item in report["plain_folders"]] == ["plain"]
    assert [item["name"] for item in report["repos_without_remote"]] == ["orphan"]

    not_cloned = {item["full_name"]: item for item in report["github_not_cloned"]}
    # cloned 以 remote URL 配對成功 → 不在清單；同名的 orphan 不得自動配對
    assert "dofliu/cloned" not in not_cloned
    assert set(not_cloned) == {"dofliu/orphan", "dofliu/cloudOnly"}
    assert not_cloned["dofliu/orphan"]["name_match_hint"] == str(root / "orphan")
    assert not_cloned["dofliu/cloudOnly"]["name_match_hint"] is None
    assert report["matching_basis"] == "canonical_remote_url_only_name_is_hint"
    # 一般資料夾以 id 引用，root 也以 id 呈現（不接受瀏覽器傳路徑）
    assert all(len(item["folder_id"]) == 16 for item in report["plain_folders"])
    assert all(len(item["root_id"]) == 16 for item in report["roots"])


# ---- init_folder ----


def test_init_folder_confirmed_only_and_id_based(workspace):
    root, _db, service = workspace
    report = service.build_report()
    folder_id = report["plain_folders"][0]["folder_id"]

    receipt = service.init_folder(folder_id)
    assert receipt["status"] == "success"
    assert (root / "plain" / ".git").is_dir()
    # 只 init：沒有 commit、沒有 remote
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root / "plain", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert log.returncode != 0  # 無任何 commit
    remotes = subprocess.run(
        ["git", "remote"], cwd=root / "plain", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert remotes.stdout.strip() == ""

    # 已 init 後同一 id 不再是合法目標（重新掃描已不在清單）
    with pytest.raises(RepoOnboardingRejected):
        service.init_folder(folder_id)
    # 未知 id（等同任意路徑）一律拒絕
    with pytest.raises(RepoOnboardingRejected):
        service.init_folder("0" * 16)


# ---- attach_remote ----


def test_attach_remote_requires_synced_repo_and_no_existing_remote(workspace):
    root, _db, service = workspace
    orphan_id = _repository_id((root / "orphan").resolve())

    # 不在已同步清單內的 repo 一律拒絕（呼叫端無法注入任意 URL）
    with pytest.raises(RepoOnboardingRejected, match="不在已同步"):
        service.attach_remote(orphan_id, "dofliu/not-synced")

    receipt = service.attach_remote(orphan_id, "dofliu/orphan")
    assert receipt["status"] == "success"
    assert receipt["remote_url"] == "https://github.com/dofliu/orphan.git"
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=root / "orphan", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    assert url == "https://github.com/dofliu/orphan.git"
    assert "未 fetch、未 push" in receipt["note"]

    # 已有 remote 的 repo 不代為變更
    with pytest.raises(RepoOnboardingRejected, match="已設定 remote"):
        service.attach_remote(orphan_id, "dofliu/orphan")
    cloned_id = _repository_id((root / "cloned").resolve())
    with pytest.raises(RepoOnboardingRejected, match="已設定 remote"):
        service.attach_remote(cloned_id, "dofliu/cloned")


# ---- clone_repo ----


def test_clone_refuses_existing_destination_and_clones_into_selected_root(
    workspace, tmp_path, monkeypatch
):
    root, _db, service = workspace
    root_id = _repository_id(root.resolve())

    # 目的地已存在（同名 orphan 目錄）→ 拒絕，絕不覆寫
    with pytest.raises(RepoOnboardingRejected, match="不覆寫"):
        service.clone_repo("dofliu/orphan", root_id)

    # clone 來源改指向本機 bare repo（URL 組法仍走同一條路徑）
    bare = tmp_path / "cloudOnly.git"
    seed = _make_repo(tmp_path / "seed")
    subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "origin", "main")
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    monkeypatch.setattr(RepoOnboarding, "_clone_url", staticmethod(lambda row: str(bare)))

    receipt = service.clone_repo("dofliu/cloudOnly", root_id)
    assert receipt["status"] == "success"
    destination = Path(receipt["destination"])
    assert destination == (root / "cloudOnly").resolve()
    assert (destination / ".git").is_dir()
    assert (destination / "README.md").is_file()

    # root_id 必須是設定內的 root
    with pytest.raises(RepoOnboardingRejected, match="root_id"):
        service.clone_repo("dofliu/cloudOnly", "0" * 16)


# ---- create_remote ----


class _FakeGitHubClient:
    def __init__(self, *, created=True):
        self.calls = []
        self.created = created

    def create_repository(self, name, *, private=True, description=""):
        self.calls.append({"name": name, "private": private})
        if not self.created:
            return {"created": False, "message": "GitHub 拒絕建立（HTTP 422）name exists"}
        return {
            "created": True,
            "full_name": f"dofliu/{name}",
            "html_url": f"https://github.com/dofliu/{name}",
            "private": private,
        }


def test_create_remote_defaults_private_and_never_pushes(workspace):
    root, _db, service = workspace
    orphan_id = _repository_id((root / "orphan").resolve())
    client = _FakeGitHubClient()

    receipt = service.create_remote(orphan_id, github_client=client)
    assert receipt["status"] == "success"
    assert client.calls == [{"name": "orphan", "private": True}]  # 預設 private
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=root / "orphan", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    assert url == "https://github.com/dofliu/orphan.git"
    # 永不代為 push：新 remote 沒有任何 remote-tracking ref
    refs = subprocess.run(
        ["git", "branch", "-r"], cwd=root / "orphan", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    assert refs == ""
    assert "永不" in receipt["note"] or "未推送" in receipt["note"]


def test_create_remote_rejects_bad_name_and_api_failure(workspace):
    root, _db, service = workspace
    orphan_id = _repository_id((root / "orphan").resolve())
    with pytest.raises(RepoOnboardingRejected, match="名稱"):
        service.create_remote(orphan_id, name="bad name!", github_client=_FakeGitHubClient())
    failing = _FakeGitHubClient(created=False)
    with pytest.raises(RepoOnboardingRejected, match="拒絕建立"):
        service.create_remote(orphan_id, github_client=failing)
    # 失敗後本機不留任何 remote
    remotes = subprocess.run(
        ["git", "remote"], cwd=root / "orphan", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    assert remotes == ""


# ---- API schema：單一目標、明確確認、拒絕多餘欄位 ----


def test_onboarding_api_schema_is_single_target_and_fail_closed():
    client = TestClient(app)
    headers = {"Origin": "http://127.0.0.1:8765"}
    # 缺 confirmation → 422
    assert client.post(
        "/api/v1/repos/onboarding-action",
        json={"action": "init_folder", "folder_id": "a" * 16},
        headers=headers,
    ).status_code == 422
    # 夾帶任意路徑／未知欄位 → 422（extra=forbid）
    assert client.post(
        "/api/v1/repos/onboarding-action",
        json={
            "action": "init_folder",
            "confirmation": "confirmed",
            "folder_id": "a" * 16,
            "path": "C:/evil",
        },
        headers=headers,
    ).status_code == 422
    # 批次形態（list of ids）不存在於 schema → 422
    assert client.post(
        "/api/v1/repos/onboarding-action",
        json={
            "action": "clone_repo",
            "confirmation": "confirmed",
            "github_full_name": ["a/b", "c/d"],
            "root_id": "a" * 16,
        },
        headers=headers,
    ).status_code == 422
    # 合法 schema 但 id 不存在 → 409（fail-closed，而非嘗試執行）
    response = client.post(
        "/api/v1/repos/onboarding-action",
        json={"action": "init_folder", "confirmation": "confirmed", "folder_id": "a" * 16},
        headers=headers,
    )
    assert response.status_code == 409
