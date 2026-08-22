import os
import time
from pathlib import Path
from typing import Set, Dict
from datetime import datetime
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from core.config import get_config
from core.database import get_db
from core.models import FileActivityEvent

logger = logging.getLogger("OmniContext.FileWatcher")


class ActivityFileHandler(FileSystemEventHandler):
    def __init__(self, allowed_exts: Set[str]):
        super().__init__()
        self.allowed_exts = allowed_exts
        self.last_events: Dict[str, float] = {}  # 用於防手震 (Debounce)
        self.debounce_seconds = 2.0

    def _is_allowed(self, path_str: str) -> bool:
        path = Path(path_str)
        if path.is_dir():
            return False
        # 檢查副檔名
        return path.suffix.lower() in self.allowed_exts

    def _process_event(self, action: str, path_str: str):
        if not self._is_allowed(path_str):
            return

        now = time.time()
        if path_str in self.last_events and (now - self.last_events[path_str]) < self.debounce_seconds:
            return
        self.last_events[path_str] = now

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

        # 寫入資料庫
        db = get_db()
        with db.session_scope() as session:
            event = FileActivityEvent(
                file_path=path_str,
                file_name=file_name,
                file_type=file_type,
                action=action,
                size_bytes=size_bytes,
                diff_summary=diff_summary,
                project_name=p.parent.name,
                timestamp=datetime.utcnow()
            )
            session.add(event)
        logger.info(f"File activity: [{action}] {file_name} ({file_type})")

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

    def start(self):
        enabled = self.cfg.get("watchers.file_watcher.enabled", True)
        if not enabled:
            logger.info("File watcher is disabled in config.")
            return

        directories = self.cfg.get("watchers.file_watcher.watch_directories", [])
        exts = set(self.cfg.get("watchers.file_watcher.extensions", [".tex", ".docx", ".md", ".pdf", ".txt"]))
        
        handler = ActivityFileHandler(allowed_exts=exts)
        scheduled_count = 0

        for d_str in directories:
            d_path = Path(d_str)
            if d_path.exists() and d_path.is_dir():
                self.observer.schedule(handler, str(d_path), recursive=True)
                logger.info(f"Watching directory: {d_path}")
                scheduled_count += 1
            else:
                logger.warning(f"Watch directory not found: {d_str}")

        if scheduled_count > 0:
            self.observer.start()
            logger.info(f"FileWatcher service started, monitoring {scheduled_count} directories.")
        else:
            logger.warning("No valid directories to monitor for FileWatcher.")

    def stop(self):
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
            logger.info("FileWatcher service stopped.")
