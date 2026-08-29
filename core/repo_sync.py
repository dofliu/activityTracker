"""受控的本機 Git repository 同步服務。

此模組刻意不做排程式同步，也不接受瀏覽器傳入任意路徑。所有 repository
都必須先由既有 ``watchers.git_watcher.repositories`` 設定探索而來；所有寫入
操作則在逐一確認後，以 argv 形式執行 Git，避免 shell injection 與意外批次變更。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.config import get_config
from watchers.git_watcher import discover_git_repos


DEFAULT_TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 1_500
_REPO_LOCKS: dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()


class RepositorySyncRejected(RuntimeError):
    """同步前置條件不成立時的可預期拒絕，不應被當成服務端例外。"""


@dataclass(frozen=True)
class RepositoryReference:
    repo_id: str
    path: Path


def _repository_id(path: Path) -> str:
    """以 canonical path 產生穩定 ID；API 從不信任客戶端提供的路徑。"""
    identity = str(path.resolve()).casefold().encode("utf-8", errors="replace")
    return hashlib.sha256(identity).hexdigest()[:16]


def _bounded_output(value: str) -> str:
    """保留可診斷訊息，遮蔽 URL userinfo 與常見 GitHub token 形態。"""
    text = (value or "").strip()
    text = re.sub(r"(https?://)[^\s/@]+@", r"\1***@", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "***REDACTED***", text)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n… output truncated"
    return text


class LocalRepositorySync:
    """只管理設定根目錄下 Git repository 的手動同步。"""

    def __init__(self, cfg: Any | None = None):
        self.cfg = cfg or get_config()

    @property
    def timeout_seconds(self) -> int:
        configured = self.cfg.get(
            "repository_sync.command_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
        )
        try:
            return max(5, min(int(configured), 120))
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS

    @property
    def status_timeout_seconds(self) -> int:
        """唯讀探測不應被單一大型 repo 長時間佔住 Dashboard worker。"""
        configured = self.cfg.get("repository_sync.status_timeout_seconds", 5)
        try:
            return max(1, min(int(configured), 20))
        except (TypeError, ValueError):
            return 5

    @property
    def status_parallelism(self) -> int:
        configured = self.cfg.get("repository_sync.status_parallelism", 8)
        try:
            return max(1, min(int(configured), 16))
        except (TypeError, ValueError):
            return 8

    @property
    def max_repositories(self) -> int:
        configured = self.cfg.get("repository_sync.max_repositories", 80)
        try:
            return max(1, min(int(configured), 200))
        except (TypeError, ValueError):
            return 80

    def _configured_roots(self) -> list[Path]:
        return [path.expanduser().resolve() for path in self.cfg.get_paths(
            "watchers.git_watcher.repositories"
        ) if path.exists() and path.is_dir()]

    def _discover_references(self) -> tuple[list[RepositoryReference], bool]:
        roots = self._configured_roots()
        max_depth = self.cfg.get("watchers.git_watcher.max_depth", 3)
        try:
            depth = max(0, min(int(max_depth), 8))
        except (TypeError, ValueError):
            depth = 3

        seen: set[str] = set()
        references: list[RepositoryReference] = []
        for candidate in discover_git_repos([str(root) for root in roots], max_depth=depth):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            # Junction / symlink 不可把同步範圍帶出使用者明示的 root。
            if not any(resolved.is_relative_to(root) for root in roots):
                continue
            key = str(resolved).casefold()
            if key in seen:
                continue
            seen.add(key)
            references.append(RepositoryReference(_repository_id(resolved), resolved))

        references.sort(key=lambda item: (item.path.name.casefold(), str(item.path).casefold()))
        truncated = len(references) > self.max_repositories
        return references[: self.max_repositories], truncated

    def _run(
        self,
        repo: RepositoryReference,
        args: Iterable[str],
        *,
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        # Dashboard 無法安全處理互動式帳密提示；由使用者既有 Git credential manager 負責。
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            ["git", *args],
            cwd=str(repo.path),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds or self.timeout_seconds,
            env=env,
            shell=False,
            check=False,
        )

    def _git_text(self, repo: RepositoryReference, *args: str) -> str | None:
        result = self._run(repo, args, timeout_seconds=self.status_timeout_seconds)
        return result.stdout.strip() if result.returncode == 0 else None

    def _worktree_counts(self, repo: RepositoryReference) -> dict[str, int]:
        result = self._run(
            repo,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            timeout_seconds=self.status_timeout_seconds,
        )
        if result.returncode != 0:
            raise RepositorySyncRejected("無法讀取 Git worktree 狀態")

        staged = unstaged = untracked = conflicted = 0
        for raw_record in result.stdout.encode("utf-8", errors="replace").split(b"\0"):
            if len(raw_record) < 2:
                continue
            status = raw_record[:2].decode("ascii", errors="replace")
            # rename/copy 的第二個 path record 沒有 XY status，略過即可。
            if len(raw_record) < 3 or raw_record[2:3] != b" ":
                continue
            if status == "??":
                untracked += 1
                continue
            if status == "!!":
                continue
            if "U" in status:
                conflicted += 1
            if status[0] != " ":
                staged += 1
            if status[1] != " ":
                unstaged += 1
        return {
            "staged_files": staged,
            "unstaged_files": unstaged,
            "untracked_files": untracked,
            "conflicted_files": conflicted,
        }

    def _operation_in_progress(self, repo: RepositoryReference) -> str | None:
        git_dir_text = self._git_text(repo, "rev-parse", "--git-dir")
        if not git_dir_text:
            return "unknown"
        git_dir = (repo.path / git_dir_text).resolve()
        markers = {
            "merge": git_dir / "MERGE_HEAD",
            "rebase": git_dir / "rebase-merge",
            "rebase_apply": git_dir / "rebase-apply",
            "cherry_pick": git_dir / "CHERRY_PICK_HEAD",
        }
        for operation, marker in markers.items():
            if marker.exists():
                return operation
        return None

    def _status_for(self, repo: RepositoryReference) -> dict[str, Any]:
        branch = self._git_text(repo, "branch", "--show-current") or None
        upstream = self._git_text(
            repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        ) if branch else None
        remote = self._git_text(repo, "config", "--get", f"branch.{branch}.remote") if branch else None
        counts = self._worktree_counts(repo)
        operation = self._operation_in_progress(repo)

        ahead = behind = None
        if upstream:
            rev_counts = self._git_text(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
            if rev_counts:
                parts = rev_counts.split()
                if len(parts) == 2 and all(part.isdigit() for part in parts):
                    ahead, behind = int(parts[0]), int(parts[1])

        if not branch:
            sync_state = "detached_head"
        elif not upstream:
            sync_state = "no_upstream"
        elif ahead is None or behind is None:
            sync_state = "upstream_unavailable"
        elif ahead and behind:
            sync_state = "diverged"
        elif ahead:
            sync_state = "ahead"
        elif behind:
            sync_state = "behind"
        else:
            sync_state = "synced"

        clean = not any(counts.values())
        actions = self._allowed_actions(
            branch=branch,
            upstream=upstream,
            remote=remote,
            ahead=ahead,
            behind=behind,
            clean=clean,
            operation=operation,
            counts=counts,
        )
        return {
            "repo_id": repo.repo_id,
            "name": repo.path.name,
            "path": str(repo.path),
            "branch": branch,
            "upstream": upstream,
            "remote": remote,
            "ahead": ahead,
            "behind": behind,
            "sync_state": sync_state,
            "remote_tracking_basis": "cached_local_remote_tracking_ref",
            "operation_in_progress": operation,
            "worktree": counts,
            "clean": clean,
            "actions": actions,
        }

    @staticmethod
    def _allowed_actions(
        *,
        branch: str | None,
        upstream: str | None,
        remote: str | None,
        ahead: int | None,
        behind: int | None,
        clean: bool,
        operation: str | None,
        counts: dict[str, int],
    ) -> dict[str, dict[str, Any]]:
        blocked = operation or ("conflicted_worktree" if counts["conflicted_files"] else None)
        upstream_ready = bool(branch and upstream and remote and ahead is not None and behind is not None)

        def state(allowed: bool, reason: str) -> dict[str, Any]:
            return {"allowed": allowed, "reason": None if allowed else reason}

        return {
            "fetch": state(bool(remote), "目前 branch 未設定遠端 remote"),
            "pull_ff_only": state(
                bool(upstream_ready and clean and not blocked and behind and not ahead),
                "僅限 clean worktree、只落後遠端且可 fast-forward 的 branch",
            ),
            "push": state(
                bool(upstream_ready and clean and not blocked and ahead and not behind),
                "僅限 clean worktree、只領先遠端且未分歧的 branch",
            ),
            "commit_staged": state(
                bool(counts["staged_files"] and not blocked),
                "請先在 Git/IDE 明確 stage 要提交的檔案，且解決衝突或進行中的 Git 操作",
            ),
        }

    def list_statuses(self) -> dict[str, Any]:
        references, truncated = self._discover_references()
        def inspect(repo: RepositoryReference) -> dict[str, Any]:
            try:
                return self._status_for(repo)
            except (OSError, subprocess.SubprocessError, RepositorySyncRejected) as exc:
                return {
                    "repo_id": repo.repo_id,
                    "name": repo.path.name,
                    "path": str(repo.path),
                    "sync_state": "unavailable",
                    "error": _bounded_output(str(exc)),
                    "actions": {},
                }

        # subprocess 的 status 查詢可受大型 untracked tree 拖慢；有限並行讓單一
        # repo 的 timeout 不會阻塞整個 Dashboard，也不會不受控地大量啟動 Git。
        with ThreadPoolExecutor(max_workers=min(self.status_parallelism, len(references) or 1)) as executor:
            repositories = list(executor.map(inspect, references))
        return {
            "repositories": repositories,
            "repository_count": len(repositories),
            "truncated": truncated,
            "remote_tracking_basis": "cached_local_remote_tracking_ref",
            "automatic_sync": False,
            "commit_policy": "staged_only_no_automatic_git_add",
        }

    def _reference_by_id(self, repo_id: str) -> RepositoryReference:
        for repo in self._discover_references()[0]:
            if repo.repo_id == repo_id:
                return repo
        raise RepositorySyncRejected("找不到已設定範圍內的 repository；請重新整理同步狀態")

    @staticmethod
    def _repo_lock(repo_id: str) -> threading.Lock:
        with _REPO_LOCKS_GUARD:
            return _REPO_LOCKS.setdefault(repo_id, threading.Lock())

    def execute(self, repo_id: str, action: str, commit_message: str | None = None) -> dict[str, Any]:
        """執行單一、已確認的 Git 動作；所有條件在 lock 中重新檢查。"""
        if action not in {"fetch", "pull_ff_only", "push", "commit_staged"}:
            raise RepositorySyncRejected("不支援的 repository 同步動作")
        repo = self._reference_by_id(repo_id)
        lock = self._repo_lock(repo.repo_id)
        if not lock.acquire(blocking=False):
            raise RepositorySyncRejected("此 repository 正在執行另一個同步動作")
        try:
            before = self._status_for(repo)
            allowed = before["actions"].get(action, {}).get("allowed", False)
            if not allowed:
                reason = before["actions"].get(action, {}).get("reason") or "前置條件未通過"
                raise RepositorySyncRejected(reason)

            if action == "commit_staged":
                message = (commit_message or "").strip()
                if not message:
                    raise RepositorySyncRejected("staged commit 必須由使用者提供 commit message")
                if len(message) > 300:
                    raise RepositorySyncRejected("commit message 最多 300 個字元")
                args = ("commit", "-m", message)
            elif action == "fetch":
                args = ("fetch", "--prune", str(before["remote"]))
            elif action == "pull_ff_only":
                args = ("pull", "--ff-only")
            else:
                # 刻意不提供 --force，且只推送目前 branch 的既有 upstream。
                args = ("push",)

            try:
                result = self._run(repo, args)
            except subprocess.TimeoutExpired as exc:
                raise RepositorySyncRejected(f"Git 動作逾時（{self.timeout_seconds} 秒）") from exc
            output = _bounded_output((result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or ""))
            after = self._status_for(repo)
            receipt = {
                "repo_id": repo.repo_id,
                "repo_name": repo.path.name,
                "action": action,
                "status": "success" if result.returncode == 0 else "failed",
                "return_code": result.returncode,
                "output": output,
                "before": before,
                "after": after,
                "automatic": False,
                "commit_policy": "staged_only_no_automatic_git_add",
            }
            if result.returncode != 0:
                raise RepositorySyncRejected(output or "Git 指令失敗")
            return receipt
        finally:
            lock.release()
