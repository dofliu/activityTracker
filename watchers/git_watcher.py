import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
import logging
from typing import List, Set, Dict, Any, Optional

from core.config import get_config
from core.database import get_db
from core.models import GitActivityEvent
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.GitWatcher")


def discover_git_repos(root_paths: List[str], max_depth: int = 3) -> List[Path]:
    """遞迴尋找指定根目錄底下所有包含 .git 的倉庫路徑"""
    found_repos: List[Path] = []
    
    for r_str in root_paths:
        root = Path(r_str)
        if not root.exists() or not root.is_dir():
            continue

        # 若本身就是 Git 倉庫
        if (root / ".git").exists():
            found_repos.append(root)
            continue

        # 否則遞迴掃描至指定深度
        def _scan(current_dir: Path, current_depth: int):
            if current_depth > max_depth:
                return
            try:
                for entry in current_dir.iterdir():
                    if entry.is_dir():
                        if entry.name in [".venv", "venv", "node_modules", ".git", "__pycache__", "site-packages"]:
                            continue
                        if (entry / ".git").exists():
                            found_repos.append(entry)
                        else:
                            _scan(entry, current_depth + 1)
            except (PermissionError, OSError):
                pass

        _scan(root, 1)

    return found_repos


class GitWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._cached_repos: List[Path] = []
        self._last_repo_discovery_time = 0.0
        self._degraded_repos: Dict[str, Dict[str, Any]] = {}
        self._successful_repos: Set[str] = set()
        self._scan_count: int = 0
        self._last_scan_at: Optional[datetime] = None
        self._healing_events: List[Dict[str, Any]] = []

    def start(self):
        enabled = self.cfg.get("watchers.git_watcher.enabled", True)
        if not enabled:
            logger.info("Git watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("GitWatcher service started (Recursive scanning enabled).")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("GitWatcher service stopped.")

    def check_health_and_heal(self) -> Dict[str, Any]:
        """自我修復：若 Git 監控線程異常中斷但設定為啟用，自動重啟"""
        enabled = self.cfg.get("watchers.git_watcher.enabled", True)
        if not enabled:
            return {"status": "disabled", "healed": False}

        if self._thread and self._thread.is_alive():
            return {"status": "healthy", "healed": False}

        logger.warning("GitWatcher worker thread dead. Initiating self-healing restart...")
        try:
            self._running = True
            self._thread = threading.Thread(target=self._scan_loop, daemon=True)
            self._thread.start()
            receipt = {
                "timestamp": get_local_now().isoformat(),
                "action": "restart_git_worker_thread",
                "status": "success"
            }
            self._healing_events.append(receipt)
            logger.info("GitWatcher self-healing restart succeeded.")
            return {"status": "healed", "healed": True, "receipt": receipt}
        except Exception as e:
            logger.error(f"GitWatcher self-healing failed: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "healed": False}

    def _get_active_repos(self) -> List[Path]:
        # 每 30 分鐘重新遍歷一次倉庫結構
        now = time.time()
        if not self._cached_repos or (now - self._last_repo_discovery_time) > 1800:
            configured_paths = [str(path) for path in self.cfg.get_paths("watchers.git_watcher.repositories")]
            max_depth = self.cfg.get("watchers.git_watcher.max_depth", 3)
            self._cached_repos = discover_git_repos(configured_paths, max_depth=max_depth)
            self._last_repo_discovery_time = now
            logger.info(f"Discovered {len(self._cached_repos)} Git repositories across configured roots.")
        return self._cached_repos

    def _scan_loop(self):
        interval = self.cfg.get("watchers.git_watcher.scan_interval_seconds", 300)
        while self._running:
            try:
                self.scan_repositories()
            except Exception as e:
                logger.error(f"Error scanning git repositories: {e}", exc_info=True)
            
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

    def scan_repositories(self):
        try:
            import git
        except ImportError:
            logger.warning("GitPython not installed. Skipping Git scanning.")
            return

        repos = self._get_active_repos()
        if not repos:
            return

        db = get_db()
        cutoff_date = (get_local_now() - timedelta(days=7)).date()  # 掃描近 7 天 commits 避免漏掉
        self._scan_count += 1
        self._last_scan_at = get_local_now()

        for repo_path in repos:
            if not repo_path.exists() or not (repo_path / ".git").exists():
                continue

            r_key = str(repo_path)
            try:
                repo = git.Repo(r_key)
                # 遍歷最多 30 個近期 commits
                for commit in repo.iter_commits(max_count=30):
                    commit_dt = datetime.fromtimestamp(commit.committed_date)
                    if commit_dt.date() < cutoff_date:
                        break

                    commit_hash = commit.hexsha[:8]
                    
                    with db.session_scope() as session:
                        existing = session.query(GitActivityEvent).filter_by(commit_hash=commit_hash).first()
                        if not existing:
                            insertions, deletions = 0, 0
                            try:
                                stats = commit.stats.total
                                insertions = stats.get("insertions", 0)
                                deletions = stats.get("deletions", 0)
                                files_count = stats.get("files", 0)
                            except Exception:
                                files_count = 1

                            branch_name = "HEAD"
                            try:
                                branch_name = repo.active_branch.name
                            except Exception:
                                pass

                            event = GitActivityEvent(
                                repo_name=repo_path.name,
                                repo_path=r_key,
                                commit_hash=commit_hash,
                                branch=branch_name,
                                author=commit.author.name,
                                message=commit.message.strip(),
                                files_changed_count=files_count,
                                insertions=insertions,
                                deletions=deletions,
                                timestamp=commit_dt
                            )
                            session.add(event)
                            logger.info(f"Git commit logged: [{repo_path.name}@{branch_name}] {commit_hash} - {commit.summary}")

                # 該倉庫掃描成功，移出 degraded 清單
                self._degraded_repos.pop(r_key, None)
                self._successful_repos.add(r_key)
            except Exception as e:
                # 局部故障隔離：單一倉庫異常不影響其他倉庫
                self._degraded_repos[r_key] = {
                    "repo_name": repo_path.name,
                    "error": str(e),
                    "timestamp": get_local_now().isoformat()
                }
                logger.debug(f"Could not read repo {repo_path}: {e}")

    def get_diagnostics(self) -> Dict[str, Any]:
        """回傳 Git 採集器健全狀態與異常倉庫隔離列表"""
        is_alive = bool(self._thread and self._thread.is_alive())
        return {
            "is_alive": is_alive,
            "state": "running" if is_alive else "stopped",
            "total_discovered_repos": len(self._cached_repos),
            "successful_repos_count": len(self._successful_repos),
            "degraded_repos_count": len(self._degraded_repos),
            "degraded_repos": list(self._degraded_repos.values()),
            "scan_count": self._scan_count,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
            "healing_events_count": len(self._healing_events),
            "recent_healing_events": self._healing_events[-5:],
        }
