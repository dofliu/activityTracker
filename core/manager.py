import logging
import threading
from typing import Dict, Any
from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary
from watchers.file_watcher import FileWatcherService
from watchers.git_watcher import GitWatcherService
from watchers.window_watcher import WindowWatcherService
from watchers.agent_log_watcher import AgentLogWatcherService
from synthesizer.scheduler import SynthesisScheduler

logger = logging.getLogger("OmniContext.Manager")


class WatcherManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(WatcherManager, cls).__new__(cls)
            cls._instance._init_manager()
        return cls._instance

    def _init_manager(self):
        self._is_running = False
        self._lock = threading.Lock()
        
        self.file_watcher = FileWatcherService()
        self.git_watcher = GitWatcherService()
        self.window_watcher = WindowWatcherService()
        self.agent_log_watcher = AgentLogWatcherService()
        self.scheduler = SynthesisScheduler()

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start_all(self) -> Dict[str, Any]:
        with self._lock:
            if self._is_running:
                return {"status": "already_running", "message": "監控服務已在運行中"}

            logger.info("Starting all watchers and scheduler...")
            self.file_watcher.start()
            self.git_watcher.start()
            self.window_watcher.start()
            self.agent_log_watcher.start()
            self.scheduler.start()
            self._is_running = True
            return {"status": "started", "message": "全景監控服務已成功啟動"}

    def stop_all(self) -> Dict[str, Any]:
        with self._lock:
            if not self._is_running:
                return {"status": "already_stopped", "message": "監控服務已處於停止狀態"}

            logger.info("Stopping all watchers and scheduler...")
            self.file_watcher.stop()
            self.git_watcher.stop()
            self.window_watcher.stop()
            self.agent_log_watcher.stop()
            self.scheduler.shutdown()
            self._is_running = False
            return {"status": "stopped", "message": "全景監控服務已停止"}

    def reload_config(self) -> Dict[str, Any]:
        """重新載入 config.yaml 並重啟監控器以套用新設定"""
        was_running = self._is_running
        if was_running:
            self.stop_all()

        get_config().load()
        self._init_manager()

        if was_running:
            self.start_all()

        return {"status": "reloaded", "message": "配置已更新並重新套用"}

    def get_status(self) -> Dict[str, Any]:
        cfg = get_config()
        db = get_db()

        from sqlalchemy import func
        with db.session_scope() as session:
            ai_count = session.query(AIPromptEvent).count()
            file_count = session.query(FileActivityEvent).count()
            git_count = session.query(GitActivityEvent).count()
            win_count = session.query(WindowEvent).count()
            summary_count = session.query(DailySummary).count()

            last_ai = session.query(func.max(AIPromptEvent.timestamp)).scalar()
            last_file = session.query(func.max(FileActivityEvent.timestamp)).scalar()
            last_git = session.query(func.max(GitActivityEvent.timestamp)).scalar()
            last_win = session.query(func.max(WindowEvent.end_time)).scalar()

        return {
            "is_running": self._is_running,
            "watchers": {
                "file_watcher": cfg.get("watchers.file_watcher.enabled", True),
                "git_watcher": cfg.get("watchers.git_watcher.enabled", True),
                "window_watcher": cfg.get("watchers.window_watcher.enabled", True),
                "agent_log_watcher": cfg.get("watchers.agent_log_watcher.enabled", True),
                "scheduler": cfg.get("synthesizer.schedule.enabled", True),
            },
            "last_events": {
                "file_watcher": last_file.strftime("%Y-%m-%d %H:%M:%S") if last_file else None,
                "git_watcher": last_git.strftime("%Y-%m-%d %H:%M:%S") if last_git else None,
                "window_watcher": last_win.strftime("%Y-%m-%d %H:%M:%S") if last_win else None,
                "agent_log_watcher": last_ai.strftime("%Y-%m-%d %H:%M:%S") if last_ai else None,
            },
            "metrics": {
                "ai_prompts_count": ai_count,
                "file_events_count": file_count,
                "git_commits_count": git_count,
                "window_events_count": win_count,
                "daily_summaries_count": summary_count,
            },
            "targets": {
                "watch_directories": cfg.get("watchers.file_watcher.watch_directories", []),
                "git_repositories": cfg.get("watchers.git_watcher.repositories", []),
                "file_extensions": cfg.get("watchers.file_watcher.extensions", []),
                "llm_provider": cfg.get("synthesizer.provider", "gemini"),
                "schedule_time": cfg.get("synthesizer.schedule.time", "23:30"),
                "periodic_interval_hours": cfg.get("synthesizer.periodic_checkpoint.interval_hours", 2)
            }
        }


def get_manager() -> WatcherManager:
    return WatcherManager()
