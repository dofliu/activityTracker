import logging
import sys
import threading
from typing import Dict, Any, Optional
from datetime import datetime
from core.time_utils import get_local_now
from core.config import get_config
from core.database import get_db
from core.models import (
    AIPromptEvent,
    DailySummary,
    FileActivityEvent,
    GitActivityEvent,
    IngestionCheckpoint,
    WindowEvent,
)
from watchers.file_watcher import FileWatcherService
from watchers.git_watcher import GitWatcherService
from watchers.window_watcher import WindowWatcherService
from watchers.agent_log_watcher import AgentLogWatcherService
from synthesizer.scheduler import SynthesisScheduler

logger = logging.getLogger("OmniContext.Manager")


def derive_monitoring_state(
    is_running: bool,
    watchers: Dict[str, bool],
    collector_runtime: Dict[str, str],
    collector_health: Dict[str, str],
) -> tuple[str, list[str]]:
    """區分 service process 存活與 collectors 實際可採集狀態。"""
    if not is_running:
        return "stopped", []
    degraded = sorted(
        key
        for key, enabled in watchers.items()
        if enabled
        and (
            collector_runtime.get(key) == "stopped"
            or collector_health.get(key) == "degraded"
        )
    )
    return ("degraded" if degraded else "healthy"), degraded


def window_probe_is_degraded(
    diagnostics: Dict[str, Any],
    *,
    interval_seconds: int,
    degraded_after_seconds: int,
) -> bool:
    """前景 probe 長時間不可用才降級；單次 lock-screen 空值不立即報錯。"""
    state = diagnostics.get("state")
    if state == "error":
        return True
    unavailable_seconds = (
        int(diagnostics.get("consecutive_unavailable", 0))
        * max(1, int(interval_seconds))
    )
    return state == "unavailable" and unavailable_seconds >= max(
        1, int(degraded_after_seconds)
    )


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
        now = get_local_now()
        with db.session_scope() as session:
            ai_count = session.query(AIPromptEvent).count()
            file_count = session.query(FileActivityEvent).count()
            git_count = session.query(GitActivityEvent).count()
            win_count = session.query(WindowEvent).count()
            summary_count = session.query(DailySummary).count()
            ai_nonempty_count = session.query(AIPromptEvent).filter(
                func.length(func.trim(AIPromptEvent.response_text)) > 0
            ).count()
            ai_final_candidate_count = session.query(AIPromptEvent).filter(
                AIPromptEvent.response_status == "final_candidate"
            ).count()
            checkpoint_count = session.query(IngestionCheckpoint).count()
            checkpoint_error_count = session.query(IngestionCheckpoint).filter(
                IngestionCheckpoint.last_error.isnot(None)
            ).count()

            last_ai = session.query(func.max(AIPromptEvent.timestamp)).scalar()
            last_file = session.query(func.max(FileActivityEvent.timestamp)).scalar()
            last_git = session.query(func.max(GitActivityEvent.timestamp)).scalar()
            last_win = session.query(func.max(WindowEvent.end_time)).scalar()

        def _calc_health(enabled: bool, last_dt: Optional[datetime]) -> str:
            if not enabled:
                return "disabled"
            if not self._is_running:
                return "stopped"
            if not last_dt:
                return "stale"
            diff_sec = (now - last_dt).total_seconds()
            if diff_sec < 1800: # < 30 分鐘
                return "healthy"
            elif diff_sec < 10800: # < 3 小時
                return "idle"
            else:
                return "stale"

        watchers_cfg = {
            "file_watcher": cfg.get("watchers.file_watcher.enabled", True),
            "git_watcher": cfg.get("watchers.git_watcher.enabled", True),
            "window_watcher": cfg.get("watchers.window_watcher.enabled", True) and sys.platform == "win32",
            "agent_log_watcher": cfg.get("watchers.agent_log_watcher.enabled", True),
            "scheduler": (
                cfg.get("synthesizer.schedule.enabled", True)
                or cfg.get("synthesizer.periodic_checkpoint.enabled", True)
                or (
                    cfg.get("usage_tracking.enabled", False)
                    and cfg.get("usage_tracking.notifications.enabled", False)
                )
            ),
        }

        def _thread_alive(service: Any) -> bool:
            thread = getattr(service, "_thread", None)
            apscheduler = getattr(service, "_apscheduler", None)
            return bool(
                (thread and thread.is_alive())
                or (apscheduler and getattr(apscheduler, "running", False))
            )

        collector_runtime = {
            "file_watcher": (
                "disabled" if not watchers_cfg["file_watcher"]
                else "running" if self.file_watcher.observer.is_alive()
                else "stopped"
            ),
            "git_watcher": (
                "disabled" if not watchers_cfg["git_watcher"]
                else "running" if _thread_alive(self.git_watcher)
                else "stopped"
            ),
            "window_watcher": (
                "disabled" if not watchers_cfg["window_watcher"]
                else "running" if _thread_alive(self.window_watcher)
                else "stopped"
            ),
            "agent_log_watcher": (
                "disabled" if not watchers_cfg["agent_log_watcher"]
                else "running" if _thread_alive(self.agent_log_watcher)
                else "stopped"
            ),
            "scheduler": (
                "disabled" if not watchers_cfg["scheduler"]
                else "running" if _thread_alive(self.scheduler)
                else "stopped"
            ),
        }

        collector_health = {
            "file_watcher": _calc_health(watchers_cfg["file_watcher"], last_file),
            "git_watcher": _calc_health(watchers_cfg["git_watcher"], last_git),
            "window_watcher": _calc_health(watchers_cfg["window_watcher"], last_win),
            "agent_log_watcher": _calc_health(watchers_cfg["agent_log_watcher"], last_ai),
            "scheduler": (
                "disabled" if not watchers_cfg["scheduler"]
                else "healthy" if collector_runtime["scheduler"] == "running"
                else "stopped"
            ),
        }

        window_diagnostics = self.window_watcher.get_diagnostics()
        agent_diagnostics = self.agent_log_watcher.get_diagnostics()
        window_interval = max(
            1, int(cfg.get("watchers.window_watcher.interval_seconds", 5))
        )
        window_degraded_after = max(
            window_interval,
            int(cfg.get("watchers.window_watcher.probe_degraded_after_seconds", 30)),
        )
        window_unavailable_seconds = (
            int(window_diagnostics.get("consecutive_unavailable", 0))
            * window_interval
        )
        window_diagnostics["degraded_after_seconds"] = window_degraded_after
        window_diagnostics["unavailable_seconds"] = window_unavailable_seconds
        if collector_runtime["window_watcher"] == "running" and window_probe_is_degraded(
            window_diagnostics,
            interval_seconds=window_interval,
            degraded_after_seconds=window_degraded_after,
        ):
            collector_health["window_watcher"] = "degraded"
        if (
            collector_runtime["agent_log_watcher"] == "running"
            and agent_diagnostics.get("state") == "degraded"
        ):
            collector_health["agent_log_watcher"] = "degraded"

        monitoring_state, degraded_collectors = derive_monitoring_state(
            self._is_running,
            watchers_cfg,
            collector_runtime,
            collector_health,
        )

        try:
            scheduled_jobs = self.scheduler.active_job_ids()
            scheduler_backend = self.scheduler.backend_name()
        except Exception:
            scheduled_jobs = []
            scheduler_backend = "unknown"

        migration_receipt = db.migration_receipt or {}
        migration_after = migration_receipt.get("after", {})
        database_migration = {
            "state": migration_after.get("state", "unknown"),
            "current_version": migration_after.get("current_version"),
            "latest_version": migration_after.get("latest_version"),
            "pending_versions": migration_after.get("pending_versions", []),
            "applied_on_start": migration_receipt.get("applied_now", []),
        }

        return {
            "is_running": self._is_running,
            "monitoring_state": monitoring_state,
            "degraded_collectors": degraded_collectors,
            "watchers": watchers_cfg,
            "collector_runtime": collector_runtime,
            "collector_health": collector_health,
            "collector_diagnostics": {
                "window_watcher": window_diagnostics,
                "agent_log_watcher": agent_diagnostics,
            },
            "scheduled_jobs": scheduled_jobs,
            "scheduler_backend": scheduler_backend,
            "database_migration": database_migration,
            "last_events": {
                "file_watcher": last_file.strftime("%Y-%m-%d %H:%M:%S") if last_file else None,
                "git_watcher": last_git.strftime("%Y-%m-%d %H:%M:%S") if last_git else None,
                "window_watcher": last_win.strftime("%Y-%m-%d %H:%M:%S") if last_win else None,
                "agent_log_watcher": last_ai.strftime("%Y-%m-%d %H:%M:%S") if last_ai else None,
            },
            "metrics": {
                "ai_prompts_count": ai_count,
                "ai_nonempty_responses_count": ai_nonempty_count,
                "ai_final_candidates_count": ai_final_candidate_count,
                "file_events_count": file_count,
                "git_commits_count": git_count,
                "window_events_count": win_count,
                "daily_summaries_count": summary_count,
                "ingestion_checkpoints_count": checkpoint_count,
                "ingestion_checkpoint_errors_count": checkpoint_error_count,
            },
            "targets": {
                "watch_directories": cfg.get("watchers.file_watcher.watch_directories", []),
                "git_repositories": cfg.get("watchers.git_watcher.repositories", []),
                "file_extensions": cfg.get("watchers.file_watcher.extensions", []),
                "llm_provider": cfg.get("synthesizer.provider", "gemini"),
                "schedule_time": cfg.get("synthesizer.schedule.time", "23:30"),
                "periodic_interval_hours": cfg.get("synthesizer.periodic_checkpoint.interval_hours", 2),
                "usage_tracking_enabled": cfg.get("usage_tracking.enabled", False),
                "usage_milestones_enabled": cfg.get("usage_tracking.notifications.enabled", False),
                "platform": sys.platform,
            }
        }


def get_manager() -> WatcherManager:
    return WatcherManager()
