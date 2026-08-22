import time
import threading
from pathlib import Path
from datetime import datetime, date
import logging
from typing import List

from core.config import get_config
from core.database import get_db
from core.models import GitActivityEvent

logger = logging.getLogger("OmniContext.GitWatcher")


class GitWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        enabled = self.cfg.get("watchers.git_watcher.enabled", True)
        if not enabled:
            logger.info("Git watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("GitWatcher service started.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("GitWatcher service stopped.")

    def _scan_loop(self):
        interval = self.cfg.get("watchers.git_watcher.scan_interval_seconds", 300)
        while self._running:
            try:
                self.scan_repositories()
            except Exception as e:
                logger.error(f"Error scanning git repositories: {e}", exc_info=True)
            
            # Sleep in short increments to allow rapid shutdown
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

    def scan_repositories(self):
        repos: List[str] = self.cfg.get("watchers.git_watcher.repositories", [])
        if not repos:
            return

        try:
            import git
        except ImportError:
            logger.warning("GitPython not installed. Skipping Git scanning.")
            return

        db = get_db()
        today = date.today()

        for repo_str in repos:
            repo_path = Path(repo_str)
            if not repo_path.exists() or not (repo_path / ".git").exists():
                continue

            try:
                repo = git.Repo(str(repo_path))
                # 取得今日的 commits
                for commit in repo.iter_commits(max_count=20):
                    commit_dt = datetime.fromtimestamp(commit.committed_date)
                    if commit_dt.date() < today:
                        # 由於 iter_commits 是從新到舊，若已早於今天可提前結束
                        break

                    commit_hash = commit.hexsha[:8]
                    
                    with db.session_scope() as session:
                        existing = session.query(GitActivityEvent).filter_by(commit_hash=commit_hash).first()
                        if not existing:
                            # 計算 diff insertions & deletions
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
                                repo_path=str(repo_path),
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
                            logger.info(f"Git commit logged: [{repo_path.name}] {commit_hash} - {commit.summary}")
            except Exception as e:
                logger.error(f"Error reading repo {repo_path}: {e}")
