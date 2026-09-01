"""P4.3 Repo Onboarding／Reconciliation（ADR-011 Addendum、FEATURE-009）。

比對使用者設定的本機 Git roots 與已同步的 GitHub metadata，處理三種情境：
一般資料夾（尚未 ``git init``）、本機 repo 沒有 remote、GitHub repo 尚未
clone 到本機。

Trust boundary（FEATURE-009，逐條落實並有 contract test）：

- **不得由同名自動配對**：本機 repo 是否「已 clone」只以 remote URL 的
  正規化比對為準；名稱相同只作為顯示提示（``name_match_hint``），絕不
  自動建立關聯或代為推論。
- **不得自動初始化或發布**：init／attach remote／create／clone 全部是
  單一目標、使用者明確確認後才執行的動作；本模組**永不執行 push**，
  首次發布由使用者用自己的 Git 工具完成。
- **不得覆寫非空目錄**：clone 目的地路徑存在（無論是否為空）一律拒絕。
- **不得批次 create/clone**：API schema 一次只接受一個目標 id。
- **不得 force reset/push**：本模組不提供任何 force 類參數。
- **不接受 dashboard 傳入任意本機路徑**（沿 ADR-011）：資料夾、repo 與
  root 一律由 server 端探索並以 canonical-path hash id 引用。
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from core.config import get_config
from core.database import get_db
from core.models import GitHubRepoState
from core.repo_sync import (
    LocalRepositorySync,
    RepositoryReference,
    _bounded_output,
    _repository_id,
)

DEFAULT_CLONE_TIMEOUT_SECONDS = 300
MAX_PLAIN_FOLDERS = 100
MAX_GITHUB_CANDIDATES = 200
_ONBOARDING_LOCKS: dict[str, threading.Lock] = {}
_ONBOARDING_LOCKS_GUARD = threading.Lock()

ONBOARDING_CLAIM_BOUNDARY = (
    "對帳只呈現觀測：已 clone 與否以 remote URL 正規化比對為準，同名僅是"
    "提示、不自動配對；init／連結 remote／建立 GitHub repo／clone 都是單一"
    "目標的確認式動作，不覆寫非空目錄、永不 force、永不代為 push。"
)

_GITHUB_REPO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class RepoOnboardingRejected(RuntimeError):
    """前置條件不成立時的可預期拒絕（HTTP 409），不視為服務端例外。"""


def _lock_for(key: str) -> threading.Lock:
    with _ONBOARDING_LOCKS_GUARD:
        return _ONBOARDING_LOCKS.setdefault(key, threading.Lock())


def canonical_github_slug(url: str | None) -> str | None:
    """把各種 GitHub remote URL 正規化為 ``owner/repo``（小寫）。

    支援 https://github.com/owner/repo(.git)、git@github.com:owner/repo(.git)、
    ssh://git@github.com/owner/repo(.git)。非 GitHub 或無法解析回 None。
    """
    text = str(url or "").strip()
    if not text:
        return None
    match = re.match(
        r"^(?:https?://(?:[^@/\s]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return f"{match.group('owner').casefold()}/{match.group('repo').casefold()}"


class RepoOnboarding:
    """對帳報告與四個確認式動作；探索與 id 規則沿用 ADR-011 同步中心。"""

    def __init__(self, cfg: Any | None = None, database: Any | None = None):
        self.cfg = cfg or get_config()
        self.database = database or get_db()
        self._sync = LocalRepositorySync(self.cfg)

    # ---- 基礎：roots／git 執行 ----

    def _roots(self) -> list[Path]:
        return self._sync._configured_roots()

    def _root_by_id(self, root_id: str) -> Path:
        for root in self._roots():
            if _repository_id(root) == root_id:
                return root
        raise RepoOnboardingRejected("root_id 不在設定的 Git roots 內；請重新掃描對帳")

    @property
    def clone_timeout_seconds(self) -> int:
        configured = self.cfg.get(
            "repository_onboarding.clone_timeout_seconds", DEFAULT_CLONE_TIMEOUT_SECONDS
        )
        try:
            return max(30, min(int(configured), 900))
        except (TypeError, ValueError):
            return DEFAULT_CLONE_TIMEOUT_SECONDS

    def _run_git(
        self, args: list[str], *, cwd: Path, timeout_seconds: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        # 與同步中心相同：不進入互動式帳密提示，credential 由使用者本機 Git 管理。
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds or self._sync.timeout_seconds,
            env=env,
            shell=False,
            check=False,
        )

    # ---- 對帳報告 ----

    def _remote_slugs(self, repo: RepositoryReference) -> tuple[list[str], list[str]]:
        """回傳 (remote 名稱清單, 正規化 GitHub slug 清單)。"""
        result = self._run_git(
            ["config", "--get-regexp", r"remote\..*\.url"],
            cwd=repo.path,
            timeout_seconds=self._sync.status_timeout_seconds,
        )
        names: list[str] = []
        slugs: list[str] = []
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                key, url = parts
                name_match = re.match(r"^remote\.(.+)\.url$", key)
                if name_match:
                    names.append(name_match.group(1))
                slug = canonical_github_slug(url)
                if slug:
                    slugs.append(slug)
        return names, slugs

    def _plain_folders(self, repo_paths: set[str]) -> tuple[list[dict[str, Any]], bool]:
        """各 root 第一層、不是 Git repo（自身或祖先皆非）的一般資料夾。"""
        folders: list[dict[str, Any]] = []
        for root in self._roots():
            root_id = _repository_id(root)
            try:
                children = sorted(root.iterdir(), key=lambda p: p.name.casefold())
            except OSError:
                continue
            for child in children:
                try:
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    resolved = child.resolve()
                    if not resolved.is_relative_to(root):
                        continue  # junction/symlink 不得帶出 root
                    if (resolved / ".git").exists():
                        continue
                    if str(resolved).casefold() in repo_paths:
                        continue
                except OSError:
                    continue
                folders.append(
                    {
                        "folder_id": _repository_id(resolved),
                        "name": resolved.name,
                        "path": str(resolved),
                        "root_id": root_id,
                    }
                )
        truncated = len(folders) > MAX_PLAIN_FOLDERS
        return folders[:MAX_PLAIN_FOLDERS], truncated

    def _github_rows(self) -> list[dict[str, Any]]:
        with self.database.session_scope() as session:
            rows = (
                session.query(GitHubRepoState)
                .order_by(GitHubRepoState.pushed_at.desc())
                .limit(MAX_GITHUB_CANDIDATES)
                .all()
            )
            return [
                {
                    "full_name": row.full_name,
                    "repo_name": row.repo_name,
                    "private": bool(row.is_private),
                    "html_url": row.html_url,
                }
                for row in rows
            ]

    def build_report(self) -> dict[str, Any]:
        references, repos_truncated = self._sync._discover_references()
        repo_paths = {str(repo.path).casefold() for repo in references}

        with ThreadPoolExecutor(
            max_workers=min(self._sync.status_parallelism, len(references) or 1)
        ) as executor:
            remote_results = list(executor.map(self._remote_slugs, references))

        local_slugs: set[str] = set()
        repos_without_remote: list[dict[str, Any]] = []
        local_names: dict[str, str] = {}
        for repo, (remote_names, slugs) in zip(references, remote_results):
            local_names.setdefault(repo.path.name.casefold(), str(repo.path))
            local_slugs.update(slugs)
            if not remote_names:
                repos_without_remote.append(
                    {
                        "repo_id": repo.repo_id,
                        "name": repo.path.name,
                        "path": str(repo.path),
                    }
                )

        plain_folders, folders_truncated = self._plain_folders(repo_paths)

        github_not_cloned: list[dict[str, Any]] = []
        for row in self._github_rows():
            slug = canonical_github_slug(row["html_url"]) or str(
                row["full_name"] or ""
            ).casefold()
            if not slug or slug in local_slugs:
                continue  # remote URL 比對成功＝已 clone（這是唯一的配對依據）
            hint_path = local_names.get(str(row["repo_name"] or "").casefold())
            github_not_cloned.append(
                {
                    **row,
                    # 同名只提示、不配對：本機有同名目錄不代表就是這個 repo。
                    "name_match_hint": hint_path,
                }
            )

        roots = [
            {"root_id": _repository_id(root), "path": str(root)} for root in self._roots()
        ]
        return {
            "roots": roots,
            "plain_folders": plain_folders,
            "plain_folders_truncated": folders_truncated,
            "repos_without_remote": repos_without_remote,
            "github_not_cloned": github_not_cloned,
            "local_repo_count": len(references),
            "local_repos_truncated": repos_truncated,
            "matching_basis": "canonical_remote_url_only_name_is_hint",
            "claim_boundary": ONBOARDING_CLAIM_BOUNDARY,
        }

    # ---- 確認式動作（單一目標；每個都在 lock 內重新驗證前置條件） ----

    def init_folder(self, folder_id: str) -> dict[str, Any]:
        """對 root 第一層的一般資料夾執行 ``git init``；不建立任何 commit。"""
        references, _ = self._sync._discover_references()
        repo_paths = {str(repo.path).casefold() for repo in references}
        folders, _ = self._plain_folders(repo_paths)
        target = next((item for item in folders if item["folder_id"] == folder_id), None)
        if target is None:
            raise RepoOnboardingRejected(
                "folder_id 不在目前掃描到的一般資料夾清單內；請重新掃描對帳"
            )
        path = Path(target["path"])
        lock = _lock_for(folder_id)
        if not lock.acquire(blocking=False):
            raise RepoOnboardingRejected("此資料夾正在執行另一個 onboarding 動作")
        try:
            if (path / ".git").exists():
                raise RepoOnboardingRejected("此資料夾已是 Git repository")
            result = self._run_git(["init"], cwd=path)
            output = _bounded_output((result.stdout or "") + (result.stderr or ""))
            if result.returncode != 0:
                raise RepoOnboardingRejected(output or "git init 失敗")
            return {
                "action": "init_folder",
                "status": "success",
                "name": target["name"],
                "path": target["path"],
                "output": output,
                "note": "只建立了空的 .git；未建立 commit、未設定 remote、未發布任何內容",
            }
        finally:
            lock.release()

    def _repo_without_remote(self, repo_id: str) -> RepositoryReference:
        repo = self._sync._reference_by_id(repo_id)
        remote_names, _ = self._remote_slugs(repo)
        if remote_names:
            raise RepoOnboardingRejected(
                f"此 repo 已設定 remote（{', '.join(sorted(remote_names)[:3])}）；"
                "不代為變更既有 remote"
            )
        return repo

    def _github_row(self, full_name: str) -> dict[str, Any]:
        wanted = str(full_name or "").strip()
        for row in self._github_rows():
            if row["full_name"] == wanted:
                return row
        raise RepoOnboardingRejected(
            "此 GitHub repo 不在已同步的清單內；請先在 GitHub 整合按「立即同步」"
        )

    @staticmethod
    def _clone_url(row: dict[str, Any]) -> str:
        """一律使用 https URL、不夾帶任何 token；私有 repo 由使用者本機的
        Git credential manager 提供認證，失敗時如實回報。"""
        return str(row["html_url"]).rstrip("/") + ".git"

    def attach_remote(self, repo_id: str, github_full_name: str) -> dict[str, Any]:
        """把已同步清單內的 GitHub repo 設為本機 repo 的 origin；不 fetch、不 push。"""
        row = self._github_row(github_full_name)
        lock = _lock_for(repo_id)
        if not lock.acquire(blocking=False):
            raise RepoOnboardingRejected("此 repository 正在執行另一個 onboarding 動作")
        try:
            repo = self._repo_without_remote(repo_id)
            url = self._clone_url(row)
            result = self._run_git(["remote", "add", "origin", url], cwd=repo.path)
            output = _bounded_output((result.stdout or "") + (result.stderr or ""))
            if result.returncode != 0:
                raise RepoOnboardingRejected(output or "git remote add 失敗")
            return {
                "action": "attach_remote",
                "status": "success",
                "repo_name": repo.path.name,
                "remote_url": url,
                "note": "只新增了 origin；未 fetch、未 push——首次推送請自行以 git push -u origin <branch> 完成",
            }
        finally:
            lock.release()

    def clone_repo(self, github_full_name: str, root_id: str) -> dict[str, Any]:
        """把已同步清單內的 GitHub repo clone 到使用者選定的 root 之下。"""
        row = self._github_row(github_full_name)
        root = self._root_by_id(root_id)
        repo_name = str(row["repo_name"] or "").strip()
        if not _GITHUB_REPO_NAME_PATTERN.match(repo_name):
            raise RepoOnboardingRejected("repo 名稱含不允許的字元，拒絕 clone")
        destination = (root / repo_name).resolve()
        if not destination.is_relative_to(root):
            raise RepoOnboardingRejected("clone 目的地不在選定 root 內，拒絕")
        lock = _lock_for(f"clone:{destination}".casefold())
        if not lock.acquire(blocking=False):
            raise RepoOnboardingRejected("相同目的地已有進行中的 clone")
        try:
            if destination.exists():
                # 「不得覆寫非空目錄」：存在即拒絕（含空目錄，避免任何歧義）。
                raise RepoOnboardingRejected(
                    f"目的地已存在（{destination.name}），不覆寫任何既有目錄"
                )
            url = self._clone_url(row)
            try:
                result = self._run_git(
                    ["clone", "--", url, str(destination)],
                    cwd=root,
                    timeout_seconds=self.clone_timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise RepoOnboardingRejected(
                    f"git clone 逾時（{self.clone_timeout_seconds} 秒）；"
                    "若留下不完整目錄請自行檢視後刪除，系統不代為刪除"
                ) from exc
            output = _bounded_output((result.stdout or "") + (result.stderr or ""))
            if result.returncode != 0:
                raise RepoOnboardingRejected(
                    (output or "git clone 失敗")
                    + "；私有 repo 需要本機 Git credential manager 已完成 GitHub 認證"
                )
            return {
                "action": "clone_repo",
                "status": "success",
                "full_name": row["full_name"],
                "destination": str(destination),
                "output": output,
                "note": "clone 完成後可在同步中心看到此 repo；未做任何本機修改或推送",
            }
        finally:
            lock.release()

    def create_remote(
        self,
        repo_id: str,
        *,
        name: str | None = None,
        private: bool = True,
        github_client: Any | None = None,
    ) -> dict[str, Any]:
        """為沒有 remote 的本機 repo 建立 GitHub repo 並設為 origin。

        預設 private；只建立空 remote 並 ``remote add``，**永不 push**——
        內容何時發布完全由使用者自己的第一次 push 決定。
        """
        lock = _lock_for(repo_id)
        if not lock.acquire(blocking=False):
            raise RepoOnboardingRejected("此 repository 正在執行另一個 onboarding 動作")
        try:
            repo = self._repo_without_remote(repo_id)
            repo_name = (name or repo.path.name).strip()
            if not _GITHUB_REPO_NAME_PATTERN.match(repo_name):
                raise RepoOnboardingRejected(
                    "repo 名稱僅允許英數與 . _ -（1–100 字元）"
                )
            if github_client is None:
                from integrations.github_client import get_github_client

                github_client = get_github_client()
            created = github_client.create_repository(repo_name, private=bool(private))
            if not created.get("created"):
                raise RepoOnboardingRejected(
                    str(created.get("message") or "GitHub 建立 repository 失敗")
                )
            url = str(created["html_url"]).rstrip("/") + ".git"
            result = self._run_git(["remote", "add", "origin", url], cwd=repo.path)
            output = _bounded_output((result.stdout or "") + (result.stderr or ""))
            if result.returncode != 0:
                raise RepoOnboardingRejected(
                    f"GitHub repo 已建立（{created.get('full_name')}）但本機 remote add 失敗：{output}"
                )
            return {
                "action": "create_remote",
                "status": "success",
                "repo_name": repo.path.name,
                "full_name": created.get("full_name"),
                "private": bool(created.get("private", private)),
                "remote_url": url,
                "note": "遠端為空 repo、本機未推送任何內容；首次發布請自行 git push -u origin <branch>",
            }
        finally:
            lock.release()
