import os
import time
import threading
import fnmatch
from pathlib import Path
from typing import Set, Dict, List, Any
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from core.config import get_config
from core.database import get_db
from core.models import FileActivityEvent
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.FileWatcher")

# 內建預設過濾黑名單
DEFAULT_IGNORES = [
    "*/site-packages/*",
    "*/.venv/*",
    "*/venv/*",
    "*/__pycache__/*",
    "*/.git/*",
    "*/node_modules/*",
    "*.dist-info/*",
    "*/.codex/*",
    "*/.gemini/*",
    "*/BladeDamage/*",
    "*/outputs/*",
    "*/results/*",
    "*/logs/*",
    "*/reports/*",
    "*/checkpoints/*",
    "*activityTracker/logs/*",
    "*activityTracker/reports/*",
    "*CASE-*",
    "*.log",
    "*.out",
    "*.csv",
    "*.tsv",
    "*.tmp",
    "~$*",
    "*.crdownload",
    "*.lock",
    "*.db",
    "*.db-journal",
    "*.db-wal",
    "*.db-shm",
    "*.pyc"
]


class ActivityFileHandler(FileSystemEventHandler):
    def __init__(self, allowed_exts: Set[str], ignore_patterns: List[str]):
        super().__init__()
        # 預設嚴格允許寫作與程式碼副檔名 (.tex, .docx, .md, .pdf, .py)，過濾 .txt 批次模擬檔
        clean_exts = {e.lower() for e in allowed_exts if e.lower() != ".txt"}
        self.allowed_exts = clean_exts if clean_exts else {".tex", ".docx", ".md", ".pdf", ".py"}
        self.ignore_patterns = DEFAULT_IGNORES + [p for p in ignore_patterns if p]
        self.last_events: Dict[str, float] = {}  # 用於防手震 (Debounce)
        self.daily_counts: Dict[str, int] = {}  # 單日單檔上限防抖 (key: YYYY-MM-DD:path)
        self.debounce_seconds = 300.0  # 同檔案 5 分鐘內只記錄一次修改，避免存檔刷屏
        self.max_events_per_day_per_file = 5  # 單一檔案每日最多紀錄 5 次

    def _is_ignored(self, path_str: str) -> bool:
        # 正規化路徑斜線為正斜線
        norm_path = path_str.replace("\\", "/")
        path_name = Path(path_str).name

        for pattern in self.ignore_patterns:
            norm_pattern = pattern.replace("\\", "/")
            if fnmatch.fnmatch(norm_path, norm_pattern) or fnmatch.fnmatch(path_name, norm_pattern):
                return True
            # 也支援部分目錄匹配
            clean_pat = norm_pattern.strip("*").strip("/")
            if clean_pat and f"/{clean_pat}/" in f"/{norm_path}/":
                return True
        return False

    def _is_allowed(self, path_str: str) -> bool:
        path = Path(path_str)
        if path.is_dir():
            return False
        
        # 1. 檢查副檔名
        if path.suffix.lower() not in self.allowed_exts:
            return False

        # 2. 檢查忽略模式
        if self._is_ignored(path_str):
            return False

        return True

    def _process_event(self, action: str, path_str: str):
        if not self._is_allowed(path_str):
            return

        now = time.time()
        # 檢查 5 分鐘 debounce (針對 modified 事件)
        if action == "modified" and path_str in self.last_events:
            if (now - self.last_events[path_str]) < self.debounce_seconds:
                return

        # 檢查單日單檔上限防呆 (防止長時間批次腳本灌爆資料庫)
        today_key = f"{get_local_now().strftime('%Y-%m-%d')}:{path_str}"
        cnt = self.daily_counts.get(today_key, 0)
        if cnt >= self.max_events_per_day_per_file:
            return

        self.last_events[path_str] = now
        self.daily_counts[today_key] = cnt + 1

        p = Path(path_str)
        file_name = p.name
        file_type = p.suffix.lower()
        size_bytes = 0
        diff_summary = None

        if p.exists() and action != "deleted":
            try:
                size_bytes = p.stat().st_size
                # 若為 Markdown 或 LaTeX，概估字數
                if file_type in [".tex", ".md", ".txt"]:
                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")
                        words = len(content.split())
                        diff_summary = f"現有字數約 {words} 字"
                    except Exception:
                        pass
            except Exception:
                pass

        # 寫入資料庫 (採用本地時間與統一專案根目錄解析)
        from core.project_engine import resolve_project_from_path
        proj_root = resolve_project_from_path(path_str)

        db = get_db()
        with db.session_scope() as session:
            event = FileActivityEvent(
                file_path=path_str,
                file_name=file_name,
                file_type=file_type,
                action=action,
                size_bytes=size_bytes,
                diff_summary=diff_summary,
                project_name=proj_root,
                timestamp=get_local_now()
            )
            session.add(event)
        logger.info(f"File activity: [{action}] {file_name} ({file_type}) in {proj_root}")

    def on_created(self, event: FileSystemEvent):
        self._process_event("created", event.src_path)

    def on_modified(self, event: FileSystemEvent):
        self._process_event("modified", event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        self._process_event("deleted", event.src_path)

    def on_moved(self, event: FileSystemEvent):
        self._process_event("moved", getattr(event, "dest_path", event.src_path))


class FileWatcherService:
    def __init__(self):
        self.observer = Observer()
        self.cfg = get_config()
        self._lock = threading.Lock()
        self._scheduled_dirs: List[str] = []
        self._failed_dirs: List[str] = []
        self._healing_events: List[Dict[str, Any]] = []

    def start(self):
        with self._lock:
            enabled = self.cfg.get("watchers.file_watcher.enabled", True)
            if not enabled:
                logger.info("File watcher is disabled in config.")
                return

            directories = self.cfg.get_paths("watchers.file_watcher.watch_directories")
            exts = set(self.cfg.get("watchers.file_watcher.extensions", [".tex", ".docx", ".md", ".pdf", ".txt", ".py"]))
            ignore_patterns = self.cfg.get("watchers.file_watcher.ignore_patterns", [])

            handler = ActivityFileHandler(allowed_exts=exts, ignore_patterns=ignore_patterns)
            self._scheduled_dirs = []
            self._failed_dirs = []

            for d_path in directories:
                if d_path.exists() and d_path.is_dir():
                    try:
                        self.observer.schedule(handler, str(d_path), recursive=True)
                        self._scheduled_dirs.append(str(d_path))
                        logger.info(f"Watching directory: {d_path}")
                    except Exception as e:
                        self._failed_dirs.append(str(d_path))
                        logger.warning(f"Failed to schedule watch on {d_path}: {e}")
                else:
                    self._failed_dirs.append(str(d_path))
                    logger.warning(f"Watch directory not found: {d_path}")

            if len(self._scheduled_dirs) > 0:
                try:
                    self.observer.start()
                    logger.info(f"FileWatcher service started, monitoring {len(self._scheduled_dirs)} directories.")
                except Exception as e:
                    logger.error(f"Failed to start FileWatcher observer: {e}", exc_info=True)
            else:
                logger.warning("No valid directories to monitor for FileWatcher.")

    def stop(self):
        with self._lock:
            if self.observer.is_alive():
                self.observer.stop()
                self.observer.join(timeout=2.0)
                logger.info("FileWatcher service stopped.")

    def check_health_and_heal(self) -> Dict[str, Any]:
        """自我修復：若檔案監控異常停止但設定為啟用，自動重啟並排程"""
        with self._lock:
            enabled = self.cfg.get("watchers.file_watcher.enabled", True)
            if not enabled:
                return {"status": "disabled", "healed": False}

            if self.observer.is_alive():
                return {"status": "healthy", "healed": False}

            logger.warning("FileWatcher observer found dead/stopped. Initiating self-healing restart...")
            try:
                # 重新建立 Observer
                self.observer = Observer()
                directories = self.cfg.get_paths("watchers.file_watcher.watch_directories")
                exts = set(self.cfg.get("watchers.file_watcher.extensions", [".tex", ".docx", ".md", ".pdf", ".txt", ".py"]))
                ignore_patterns = self.cfg.get("watchers.file_watcher.ignore_patterns", [])

                handler = ActivityFileHandler(allowed_exts=exts, ignore_patterns=ignore_patterns)
                self._scheduled_dirs = []
                self._failed_dirs = []

                for d_path in directories:
                    if d_path.exists() and d_path.is_dir():
                        try:
                            self.observer.schedule(handler, str(d_path), recursive=True)
                            self._scheduled_dirs.append(str(d_path))
                        except Exception as e:
                            self._failed_dirs.append(str(d_path))
                    else:
                        self._failed_dirs.append(str(d_path))

                if len(self._scheduled_dirs) > 0:
                    self.observer.start()
                    receipt = {
                        "timestamp": get_local_now().isoformat(),
                        "action": "restart_observer",
                        "status": "success",
                        "monitored_count": len(self._scheduled_dirs)
                    }
                    self._healing_events.append(receipt)
                    logger.info(f"FileWatcher self-healing succeeded: {len(self._scheduled_dirs)} directories re-scheduled.")
                    return {"status": "healed", "healed": True, "receipt": receipt}
                else:
                    return {"status": "no_valid_directories", "healed": False}
            except Exception as e:
                logger.error(f"FileWatcher self-healing failed: {e}", exc_info=True)
                return {"status": "error", "error": str(e), "healed": False}

    def get_diagnostics(self) -> Dict[str, Any]:
        """提供採集器健全度與修復診斷資訊"""
        is_alive = self.observer.is_alive()
        return {
            "is_alive": is_alive,
            "state": "running" if is_alive else "stopped",
            "scheduled_directories_count": len(self._scheduled_dirs),
            "scheduled_directories": self._scheduled_dirs,
            "failed_directories": self._failed_dirs,
            "healing_events_count": len(self._healing_events),
            "recent_healing_events": self._healing_events[-5:],
            "last_healed_at": self._healing_events[-1]["timestamp"] if self._healing_events else None
        }
