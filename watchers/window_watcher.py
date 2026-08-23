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
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.WindowWatcher")


def get_active_window_info() -> Tuple[Optional[str], Optional[str]]:
    """取得當前 Windows 前景作用中視窗的 (應用程式名稱, 視窗標題)，若無有效視窗回傳 (None, None)"""
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or hwnd == 0:
            return None, None

        title = win32gui.GetWindowText(hwnd)
        if not title or not title.strip():
            return None, None

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process = psutil.Process(pid)
            app_name = process.name()
        except Exception:
            app_name = "Unknown"

        if app_name.lower() in ("idle", "unknown") and title.lower() in ("idle", ""):
            return None, None

        return app_name, title.strip()
    except ImportError:
        # Fallback via ctypes
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd or hwnd == 0:
                return None, None
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return None, None
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            val = buff.value.strip() if buff.value else ""
            if not val or val.lower() == "idle":
                return None, None
            return "WindowsApp", val
        except Exception:
            return None, None


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
        self._current_start_time: datetime = get_local_now()

    def start(self):
        enabled = self.cfg.get("watchers.window_watcher.enabled", True)
        if not enabled:
            logger.info("Window watcher is disabled in config.")
            return

        self._running = True
        self._current_start_time = get_local_now()
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
        """將上一個視窗的停留時間結算寫入資料庫 (過濾假 Idle 與過短事件)"""
        if not self._current_app or not self._current_title:
            return

        if self._current_app.lower() in ("idle", "unknown") and self._current_title.lower() in ("idle", ""):
            self._current_app = ""
            self._current_title = ""
            return

        now = get_local_now()
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

        self._current_app = ""
        self._current_title = ""

    def _monitor_loop(self):
        interval = self.cfg.get("watchers.window_watcher.interval_seconds", 5)
        ignore_titles = set(self.cfg.get("watchers.window_watcher.ignore_titles", []))

        while self._running:
            try:
                app_name, title = get_active_window_info()
                if not app_name or not title:
                    # 拿不到前景視窗（如背景服務執行或鎖定螢幕）：結算當前視窗，不寫入偽造 Idle
                    if self._current_app:
                        self._flush_current_window()
                else:
                    if title not in ignore_titles:
                        if app_name != self._current_app or title != self._current_title:
                            self._flush_current_window()
                            self._current_app = app_name
                            self._current_title = title
                            self._current_start_time = get_local_now()
                        else:
                            # 防呆：單一視窗超過 1800 秒（30分鐘）強制階段性結算
                            now = get_local_now()
                            if (now - self._current_start_time).total_seconds() > 1800:
                                self._flush_current_window()
                                self._current_app = app_name
                                self._current_title = title
                                self._current_start_time = now
            except Exception as e:
                logger.error(f"Error in window monitoring: {e}")

            time.sleep(interval)
