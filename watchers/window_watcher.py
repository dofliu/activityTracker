import time
import threading
from datetime import datetime
import logging
from typing import Optional, Tuple, Dict, Any
import ctypes
import os
import sys

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
    elif any(k in title_lower for k in ["gemini", "chatgpt", "claude", "copilot", "deepseek"]):
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
        self.last_written_at: Optional[datetime] = None
        self._probe_lock = threading.Lock()
        self._probe_state = "not_started"
        self._last_probe_at: Optional[datetime] = None
        self._last_probe_success_at: Optional[datetime] = None
        self._consecutive_unavailable = 0
        self._last_probe_error_code: Optional[str] = None

    def _record_probe(self, app_name: Optional[str], title: Optional[str]) -> None:
        now = get_local_now()
        with self._probe_lock:
            self._last_probe_at = now
            if app_name and title:
                self._probe_state = "healthy"
                self._last_probe_success_at = now
                self._consecutive_unavailable = 0
                self._last_probe_error_code = None
                return
            self._probe_state = "unavailable"
            self._consecutive_unavailable += 1
            self._last_probe_error_code = "foreground_unavailable"

    def _record_probe_error(self, exc: Exception) -> None:
        with self._probe_lock:
            self._last_probe_at = get_local_now()
            self._probe_state = "error"
            self._consecutive_unavailable += 1
            self._last_probe_error_code = (
                "permission_denied" if isinstance(exc, PermissionError) else "probe_error"
            )

    def get_diagnostics(self) -> dict:
        """回傳不含視窗標題／應用名稱的 probe health snapshot。"""
        with self._probe_lock:
            return {
                "state": self._probe_state,
                "last_probe_at": (
                    self._last_probe_at.isoformat(timespec="seconds")
                    if self._last_probe_at
                    else None
                ),
                "last_success_at": (
                    self._last_probe_success_at.isoformat(timespec="seconds")
                    if self._last_probe_success_at
                    else None
                ),
                "consecutive_unavailable": self._consecutive_unavailable,
                "last_error_code": self._last_probe_error_code,
            }

    def start(self):
        enabled = self.cfg.get("watchers.window_watcher.enabled", True)
        if not enabled:
            logger.info("Window watcher is disabled in config.")
            return
        if sys.platform != "win32":
            logger.warning("Window watcher is currently supported on Windows only; collector disabled.")
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

    def check_health_and_heal(self) -> Dict[str, Any]:
        """自我修復：若 Windows 前景視窗監控線程異常終止，自動重啟"""
        enabled = self.cfg.get("watchers.window_watcher.enabled", True) and sys.platform == "win32"
        if not enabled:
            return {"status": "disabled", "healed": False}

        if self._thread and self._thread.is_alive():
            return {"status": "healthy", "healed": False}

        logger.warning("WindowWatcher thread found dead. Initiating self-healing restart...")
        try:
            self._running = True
            self._current_start_time = get_local_now()
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            receipt = {
                "timestamp": get_local_now().isoformat(),
                "action": "restart_window_thread",
                "status": "success"
            }
            logger.info("WindowWatcher self-healing restart succeeded.")
            return {"status": "healed", "healed": True, "receipt": receipt}
        except Exception as e:
            logger.error(f"WindowWatcher self-healing failed: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "healed": False}

    def _flush_current_window(self):
        """將上一個視窗的停留時間結算寫入資料庫 (過濾假 Idle 與過短事件)"""
        app = self._current_app
        title = self._current_title
        start_t = self._current_start_time

        # 先重設狀態，確保即使寫入失敗也不會造成死循環阻塞
        self._current_app = ""
        self._current_title = ""

        if not app or not title:
            return

        if app.lower() in ("idle", "unknown") and title.lower() in ("idle", ""):
            return

        now = get_local_now()
        duration = (now - start_t).total_seconds()
        
        # 過短的時間（小於 3 秒）視為閃退或快速切換，忽略以節省空間
        if duration >= 3.0:
            try:
                db = get_db()
                category = categorize_window(app, title)
                with db.session_scope() as session:
                    event = WindowEvent(
                        start_time=start_t,
                        end_time=now,
                        duration_seconds=duration,
                        app_name=app,
                        window_title=title,
                        category=category
                    )
                    session.add(event)
                self.last_written_at = now
                logger.info(f"Recorded window focus: [{app}] {title[:50]} ({duration:.1f}s)")
            except Exception as e:
                logger.error(f"Failed to save window event [{app}] {title}: {e}", exc_info=True)

    def _monitor_loop(self):
        interval = self.cfg.get("watchers.window_watcher.interval_seconds", 5)
        ignore_titles = set(self.cfg.get("watchers.window_watcher.ignore_titles", []))
        # 心跳週期：定期把「實際讀到什麼」寫進日誌，讓靜默失效可以直接定位是讀不到還是寫不進
        heartbeat_seconds = self.cfg.get("watchers.window_watcher.heartbeat_minutes", 5) * 60
        last_heartbeat = 0.0

        while self._running:
            try:
                app_name, title = get_active_window_info()
                self._record_probe(app_name, title)

                if time.time() - last_heartbeat >= heartbeat_seconds:
                    last_heartbeat = time.time()
                    if app_name and title:
                        logger.info(f"WindowWatcher heartbeat: 前景視窗 = {app_name} | {title[:60]}")
                    else:
                        logger.warning("WindowWatcher heartbeat: 讀不到前景視窗 (GetForegroundWindow 回傳空值)")

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
                self._record_probe_error(e)
                logger.error(f"Error in window monitor loop: {e}", exc_info=True)

            time.sleep(interval)
