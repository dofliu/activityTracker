"""受控的本機 Git repository 同步服務。

此模組刻意不做排程式同步，也不接受瀏覽器傳入任意路徑。所有 repository
都必須先由既有 ``watchers.git_watcher.repositories`` 設定探索而來；所有寫入
操作則在逐一確認後，以 argv 形式執行 Git，避免 shell injection 與意外批次變更。

ADR-011 Addendum B（2026-09-02）補上「全覽與批次」：``fetch --prune`` 只更新
remote-tracking ref，可一鍵對全部 repo 執行；批次 pull／push 則必須先由
``batch_plan`` 列出「目前符合前置條件」的清單、由使用者確認該清單後，逐一
在 lock 內重檢再執行；批次 push 另有獨立開關且預設關閉。任何批次都不會
放寬單一動作的前置條件，也仍然沒有排程自動同步。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
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

    @property
    def dashboard_recent_limit(self) -> int:
        """Dashboard 只保留可讀的近期工作清單，完整範圍仍供 action allowlist 使用。"""
        configured = self.cfg.get("repository_sync.dashboard_recent_limit", 10)
        try:
            return max(1, min(int(configured), 50))
        except (TypeError, ValueError):
            return 10

    @property
    def batch_push_allowed(self) -> bool:
        """批次 push 獨立開關；預設關閉（單一 repo 的手動 push 不受影響）。"""
        return bool(self.cfg.get("repository_sync.batch.allow_push", False))

    @property
    def batch_max_repositories(self) -> int:
        configured = self.cfg.get("repository_sync.batch.max_repositories", 50)
        try:
            return max(1, min(int(configured), 200))
        except (TypeError, ValueError):
            return 50

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

    @staticmethod
    def _changed_path_mtime(repo: RepositoryReference, raw_path: bytes) -> float:
        """只讀取 Git 回報的相對路徑；避免把解析出的 path 帶出 repo root。"""
        try:
            relative = Path(raw_path.decode("utf-8", errors="replace"))
            candidate = (repo.path / relative).resolve()
            if not candidate.is_relative_to(repo.path) or not candidate.is_file():
                return 0.0
            return candidate.stat().st_mtime
        except (OSError, ValueError):
            return 0.0

    def _worktree_counts(self, repo: RepositoryReference) -> tuple[dict[str, int], float]:
        result = self._run(
            repo,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            timeout_seconds=self.status_timeout_seconds,
        )
        if result.returncode != 0:
            raise RepositorySyncRejected("無法讀取 Git worktree 狀態")

        staged = unstaged = untracked = conflicted = 0
        latest_worktree_mtime = 0.0
        previous_was_rename_or_copy = False
        for raw_record in result.stdout.encode("utf-8", errors="replace").split(b"\0"):
            if len(raw_record) < 2:
                continue
            status = raw_record[:2].decode("ascii", errors="replace")
            if len(raw_record) < 3 or raw_record[2:3] != b" ":
                # rename/copy 的第二個 path record 同樣要計入最近修改時間。
                if previous_was_rename_or_copy:
                    latest_worktree_mtime = max(
                        latest_worktree_mtime,
                        self._changed_path_mtime(repo, raw_record),
                    )
                previous_was_rename_or_copy = False
                continue
            raw_path = raw_record[3:]
            latest_worktree_mtime = max(
                latest_worktree_mtime,
                self._changed_path_mtime(repo, raw_path),
            )
            previous_was_rename_or_copy = status[0] in {"R", "C"} or status[1] in {"R", "C"}
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
        return ({
            "staged_files": staged,
            "unstaged_files": unstaged,
            "untracked_files": untracked,
            "conflicted_files": conflicted,
        }, latest_worktree_mtime)

    def _last_activity(self, repo: RepositoryReference, worktree_mtime: float) -> tuple[str | None, str, float]:
        """以 dirty worktree 優先、最後 commit 次之，讓排序反映近期實際編修。"""
        commit_epoch = 0.0
        commit_text = self._git_text(repo, "log", "-1", "--format=%ct")
        if commit_text and commit_text.isdigit():
            commit_epoch = float(commit_text)
        if worktree_mtime > commit_epoch:
            epoch, source = worktree_mtime, "worktree_change"
        elif commit_epoch:
            epoch, source = commit_epoch, "local_commit"
        else:
            epoch, source = 0.0, "unknown"
        timestamp = datetime.fromtimestamp(epoch).astimezone().isoformat() if epoch else None
        return timestamp, source, epoch

    def _last_commit_epoch(self, repo: RepositoryReference) -> float:
        """第一階段只讀取最後 commit，快速決定 Dashboard 的近期候選清單。"""
        commit_text = self._git_text(repo, "log", "-1", "--format=%ct")
        return float(commit_text) if commit_text and commit_text.isdigit() else 0.0

    def _last_fetch_at(self, repo: RepositoryReference) -> str | None:
        """FETCH_HEAD 的修改時間＝本機上次成功 fetch 的時刻；沒有就是從未 fetch。"""
        git_dir_text = self._git_text(repo, "rev-parse", "--git-dir")
        if not git_dir_text:
            return None
        marker = (repo.path / git_dir_text).resolve() / "FETCH_HEAD"
        try:
            return datetime.fromtimestamp(marker.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        except OSError:
            return None

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
        counts, worktree_mtime = self._worktree_counts(repo)
        operation = self._operation_in_progress(repo)
        last_activity_at, last_activity_source, activity_epoch = self._last_activity(
            repo, worktree_mtime
        )

        ahead = behind = None
        if upstream:
            rev_counts = self._git_text(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
            if rev_counts:
                parts = rev_counts.split()
                if len(parts) == 2 and all(part.isdigit() for part in parts):
                    ahead, behind = int(parts[0]), int(parts[1])

        last_fetch_at = self._last_fetch_at(repo)

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
            "last_fetch_at": last_fetch_at,
            "operation_in_progress": operation,
            "worktree": counts,
            "clean": clean,
            "last_activity_at": last_activity_at,
            "last_activity_source": last_activity_source,
            "_sort_last_activity_epoch": activity_epoch,
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

    def list_statuses(self, scope: str = "recent") -> dict[str, Any]:
        """``recent``：近期 N 個 repo 的完整狀態（Dashboard 卡片）；``all``：全部。

        兩者都只讀本機 cached remote-tracking ref，不連網。``all`` 會對每個
        repo 跑 git status，成本與 repo 數成正比，因此由使用者展開表格時才呼叫。
        """
        if scope not in ("recent", "all"):
            raise RepositorySyncRejected("scope 只接受 recent 或 all")
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

        # 先用單一 log query 排定候選，避免為 60+ 個未顯示 repo 做昂貴的 worktree
        # 掃描；完整 status 僅限近期清單。這個 ranking 不會改變 action allowlist。
        with ThreadPoolExecutor(max_workers=min(self.status_parallelism, len(references) or 1)) as executor:
            ranking_epochs = list(executor.map(self._last_commit_epoch, references))
        ranked_references = [
            repo for repo, _ in sorted(
                zip(references, ranking_epochs),
                key=lambda item: (-item[1], item[0].path.name.casefold(), str(item[0].path).casefold()),
            )
        ]
        selected_references = (
            ranked_references if scope == "all" else ranked_references[: self.dashboard_recent_limit]
        )

        # subprocess 的 status 查詢可受大型 untracked tree 拖慢；有限並行讓單一
        # repo 的 timeout 不會阻塞整個 Dashboard，也不會不受控地大量啟動 Git。
        with ThreadPoolExecutor(max_workers=min(self.status_parallelism, len(selected_references) or 1)) as executor:
            repositories = list(executor.map(inspect, selected_references))
        repositories.sort(
            key=lambda item: (
                -float(item.get("_sort_last_activity_epoch") or 0),
                str(item.get("name") or "").casefold(),
            )
        )
        repository_count = len(references)
        attention_count = sum(
            1 for repository in repositories
            if repository.get("sync_state") != "synced" or not repository.get("clean", False)
        )
        displayed = repositories if scope == "all" else repositories[: self.dashboard_recent_limit]
        for repository in displayed:
            repository.pop("_sort_last_activity_epoch", None)
        return {
            "scope": scope,
            "repositories": displayed,
            "repository_count": repository_count,
            "displayed_count": len(displayed),
            "recent_limit": self.dashboard_recent_limit,
            "attention_count": attention_count,
            "attention_scope": "displayed_repositories",
            "summary": self._summarize(displayed),
            "batch": {
                "fetch_all": True,
                "pull_ff_only": True,
                "push": self.batch_push_allowed,
                "max_repositories": self.batch_max_repositories,
            },
            "recent_ranking_basis": "last_local_commit_then_displayed_worktree_activity",
            "truncated": truncated,
            "remote_tracking_basis": "cached_local_remote_tracking_ref",
            "automatic_sync": False,
            "commit_policy": "staged_only_no_automatic_git_add",
        }

    @staticmethod
    def _summarize(repositories: list[dict[str, Any]]) -> dict[str, int]:
        summary = {
            "synced": 0, "behind": 0, "ahead": 0, "diverged": 0,
            "no_upstream": 0, "dirty": 0, "unavailable": 0,
        }
        for repo in repositories:
            state = repo.get("sync_state")
            if state in ("synced", "behind", "ahead", "diverged"):
                summary[state] += 1
            elif state in ("no_upstream", "detached_head", "upstream_unavailable"):
                summary["no_upstream"] += 1
            else:
                summary["unavailable"] += 1
            if repo.get("clean") is False:
                summary["dirty"] += 1
        return summary

    def fetch_all(self) -> dict[str, Any]:
        """對全部有 remote 的 repo 執行 ``fetch --prune``（只更新 remote-tracking ref）。

        這是唯一允許「一鍵全部」而不需列清單確認的動作：它不改 worktree、不改
        本機 branch、不改遠端。正在執行其他動作的 repo 會被跳過並如實列出。
        """
        references, truncated = self._discover_references()

        def one(repo: RepositoryReference) -> dict[str, Any]:
            lock = self._repo_lock(repo.repo_id)
            if not lock.acquire(blocking=False):
                return {"repo_id": repo.repo_id, "repo_name": repo.path.name, "status": "skipped",
                        "reason": "此 repository 正在執行另一個同步動作"}
            try:
                branch = self._git_text(repo, "branch", "--show-current") or None
                remote = self._git_text(repo, "config", "--get", f"branch.{branch}.remote") if branch else None
                if not remote:
                    remote = self._git_text(repo, "config", "--get", "remote.origin.url") and "origin"
                if not remote:
                    return {"repo_id": repo.repo_id, "repo_name": repo.path.name, "status": "skipped",
                            "reason": "沒有可 fetch 的 remote"}
                try:
                    result = self._run(repo, ("fetch", "--prune", remote))
                except subprocess.TimeoutExpired:
                    return {"repo_id": repo.repo_id, "repo_name": repo.path.name, "status": "failed",
                            "reason": f"Git fetch 逾時（{self.timeout_seconds} 秒）"}
                output = _bounded_output((result.stdout or "") + "\n" + (result.stderr or ""))
                return {
                    "repo_id": repo.repo_id, "repo_name": repo.path.name,
                    "status": "success" if result.returncode == 0 else "failed",
                    "return_code": result.returncode,
                    "reason": None if result.returncode == 0 else (output or "Git fetch 失敗"),
                }
            except (OSError, subprocess.SubprocessError) as exc:
                return {"repo_id": repo.repo_id, "repo_name": repo.path.name, "status": "failed",
                        "reason": _bounded_output(str(exc))}
            finally:
                lock.release()

        with ThreadPoolExecutor(max_workers=min(self.status_parallelism, len(references) or 1)) as executor:
            results = list(executor.map(one, references))
        counts = {"success": 0, "failed": 0, "skipped": 0}
        for item in results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {
            "action": "fetch_all",
            "repository_count": len(references),
            "truncated": truncated,
            "counts": counts,
            "results": results,
            "worktree_changed": False,
            "automatic": False,
            "claim_boundary": "只更新各 repo 的 remote-tracking refs；不改 worktree、本機 branch 或遠端。",
        }

    _BATCH_ACTIONS = ("pull_ff_only", "push")

    def batch_plan(self, action: str) -> dict[str, Any]:
        """列出目前符合 ``action`` 前置條件的 repo；不執行任何動作。

        使用者確認的是這份清單；``batch_execute`` 只接受清單內的 repo_id，
        且每個 repo 執行前仍會在 lock 內重檢一次。
        """
        if action not in self._BATCH_ACTIONS:
            raise RepositorySyncRejected("批次只支援 pull_ff_only 與 push")
        if action == "push" and not self.batch_push_allowed:
            raise RepositorySyncRejected(
                "批次 push 未啟用（repository_sync.batch.allow_push=false）；單一 repo 的 Push 仍可逐一執行"
            )
        statuses = self.list_statuses(scope="all")
        eligible: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for repo in statuses["repositories"]:
            state = (repo.get("actions") or {}).get(action) or {}
            entry = {
                "repo_id": repo["repo_id"], "name": repo["name"], "branch": repo.get("branch"),
                "upstream": repo.get("upstream"), "ahead": repo.get("ahead"), "behind": repo.get("behind"),
                "sync_state": repo.get("sync_state"), "last_fetch_at": repo.get("last_fetch_at"),
            }
            if state.get("allowed"):
                eligible.append(entry)
            else:
                excluded.append({**entry, "reason": state.get("reason") or repo.get("error") or "前置條件未通過"})
        capped = eligible[: self.batch_max_repositories]
        return {
            "action": action,
            "eligible": capped,
            "eligible_count": len(capped),
            "excluded": excluded,
            "excluded_count": len(excluded),
            "capped": len(eligible) > len(capped),
            "max_repositories": self.batch_max_repositories,
            "remote_tracking_basis": "cached_local_remote_tracking_ref",
            "claim_boundary": "清單依本機 cached remote-tracking ref 判定；執行時每個 repo 會再重檢一次，不符者跳過。",
        }

    def batch_execute(self, action: str, repo_ids: list[str]) -> dict[str, Any]:
        """依使用者確認的 repo_id 清單逐一執行；每個 repo 都走 ``execute`` 的重檢與 lock。"""
        if action not in self._BATCH_ACTIONS:
            raise RepositorySyncRejected("批次只支援 pull_ff_only 與 push")
        if action == "push" and not self.batch_push_allowed:
            raise RepositorySyncRejected(
                "批次 push 未啟用（repository_sync.batch.allow_push=false）"
            )
        unique_ids = list(dict.fromkeys(str(item) for item in repo_ids))
        if not unique_ids:
            raise RepositorySyncRejected("批次清單為空")
        if len(unique_ids) > self.batch_max_repositories:
            raise RepositorySyncRejected(f"單次批次最多 {self.batch_max_repositories} 個 repository")
        results: list[dict[str, Any]] = []
        counts = {"success": 0, "failed": 0, "skipped": 0}
        # 逐一（而非並行）執行：pull/push 可能觸發 hooks 或 credential helper，
        # 並行會讓失敗原因難以歸因，也可能同時彈出多個系統提示。
        for repo_id in unique_ids:
            try:
                receipt = self.execute(repo_id, action)
                results.append({
                    "repo_id": repo_id, "repo_name": receipt["repo_name"], "status": "success",
                    "return_code": receipt["return_code"],
                    "after_sync_state": (receipt.get("after") or {}).get("sync_state"),
                })
                counts["success"] += 1
            except RepositorySyncRejected as exc:
                message = str(exc)
                status = "skipped" if ("前置" in message or "僅限" in message or "正在執行" in message or "找不到" in message) else "failed"
                name = None
                try:
                    name = self._reference_by_id(repo_id).path.name
                except RepositorySyncRejected:
                    pass
                results.append({"repo_id": repo_id, "repo_name": name, "status": status, "reason": message})
                counts[status] += 1
        return {
            "action": action,
            "requested": len(unique_ids),
            "counts": counts,
            "results": results,
            "automatic": False,
            "force": False,
            "claim_boundary": "每個 repo 執行前重檢 clean worktree、只落後／只領先且無分歧；不符者跳過，永不 force。",
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
