import time
import threading
from datetime import datetime
import logging
from typing import Optional, Tuple
import ctypes
import os

from core.config import get_config
from core.database import get_db
from core.models import WindowEvent

logger = logging.getLogger("OmniContext.WindowWatcher")


def get_active_window_info() -> Tuple[str, str]:
    """取得當前 Windows 前景作用中視窗的 (應用程式名稱, 視窗標題)"""
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "Idle", "Idle"

        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process = psutil.Process(pid)
            app_name = process.name()
        except Exception:
            app_name = "Unknown"

        return app_name, title
    except ImportError:
        # Fallback via ctypes
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            return "WindowsApp", buff.value or "Idle"
        except Exception:
            return "System", "Desktop"


def categorize_window(app_name: str, title: str) -> str:
    """根據應用程式與標題智能歸類活動分類"""
    app_lower = app_name.lower()
    title_lower = title.lower()

    if any(k in app_lower for k in ["code", "pycharm", "cursor", "devenv", "clion"]):
        return "Coding / Development"
    elif any(k in title_lower for k in ["latex", "overleaf", "texstudio", "word", "winword", "zotero", "acrobat", "arxiv", "paper"]):
        return "Research / Paper Writing"
    elif any(k in title_lower for k in ["gemini", "chatgpt", "claude", "manus", "copilot", "deepseek"]):
        return "AI Assistance / Research"
    elif any(k in app_lower for k in ["slack", "teams", "discord", "outlook", "mail", "wechat", "telegram"]):
        return "Communication / Email"
    elif any(k in app_lower for k in ["chrome", "edge", "firefox", "brave"]):
        return "Web Browsing"
    else:
        return "General Activity"


class WindowWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._current_app: str = ""
        self._current_title: str = ""
        self._current_start_time: datetime = datetime.utcnow()

    def start(self):
        enabled = self.cfg.get("watchers.window_watcher.enabled", True)
        if not enabled:
            logger.info("Window watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("WindowWatcher service started.")

    def stop(self):
        self._flush_current_window()
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("WindowWatcher service stopped.")

    def _flush_current_window(self):
        """將上一個視窗的停留時間結算寫入資料庫"""
        if not self._current_app or not self._current_title:
            return

        now = datetime.utcnow()
        duration = (now - self._current_start_time).total_seconds()
        
        # 過短的時間（小於 3 秒）視為閃退或快速切換，忽略以節省空間
        if duration >= 3.0:
            db = get_db()
            category = categorize_window(self._current_app, self._current_title)
            with db.session_scope() as session:
                event = WindowEvent(
                    start_time=self._current_start_time,
                    end_time=now,
                    duration_seconds=duration,
                    app_name=self._current_app,
                    window_title=self._current_title,
                    category=category
                )
                session.add(event)

    def _monitor_loop(self):
        interval = self.cfg.get("watchers.window_watcher.interval_seconds", 5)
        ignore_titles = set(self.cfg.get("watchers.window_watcher.ignore_titles", []))

        while self._running:
            try:
                app_name, title = get_active_window_info()
                if title and title not in ignore_titles:
                    if app_name != self._current_app or title != self._current_title:
                        # 視窗切換，結算前一個
                        self._flush_current_window()
                        self._current_app = app_name
                        self._current_title = title
                        self._current_start_time = datetime.utcnow()
            except Exception as e:
                logger.error(f"Error in window monitoring: {e}")

            time.sleep(interval)
