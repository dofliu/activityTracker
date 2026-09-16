"""Windows 原生桌面通知 (Toast)：**只負責送達，不組裝內容**。

刻意不依賴 winotify / plyer：直接以 PowerShell 呼叫 WinRT ToastNotificationManager，
零額外安裝、零帳號設定，符合「不需要太多設定」的目標。
若 WinRT 不可用（舊版 Windows 或政策限制），自動降級為 MessageBox。

2026-09-16（TODO D5）之前，這個模組同時也自己組晨報／晚報／停滯／里程碑的內容，
於是同一件事在這裡與 `notifiers/messages.py` 各寫一遍、兩邊會漂移（例如未歸戶
收容桶這裡濾、那裡沒濾）。現在內容一律由 `notifiers.messages` 組、由
`notifiers.channels.DesktopChannel` 轉成 toast、由 `notifiers.secretary_push` 扇出；
這裡只剩 transport：把「標題＋幾行字」送到 Windows，並留下不含內文的送達收據。
"""
import logging
import subprocess
import sys
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path
from typing import List, Optional

from core.config import get_config

logger = logging.getLogger("OmniContext.DesktopNotifier")

# 使用 PowerShell 已註冊的 AppUserModelID，免去自行註冊捷徑的麻煩
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
__TOAST_XML__
'@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APP_ID__').Show($toast)
"""


class DesktopNotifier:
    """WinRT toast transport；``last_delivery_receipt`` 只記狀態與 transport，不含內文。"""

    def __init__(self, cfg=None):
        self.cfg = cfg or get_config()
        self.last_delivery_receipt: dict | None = None

    def is_enabled(self) -> bool:
        return bool(self.cfg.get("notifiers.desktop.enabled", True)) and sys.platform == "win32"

    # ------------------------------------------------------------------
    # 底層送出
    # ------------------------------------------------------------------
    def send(
        self,
        title: str,
        lines: List[str],
        launch_url: Optional[str] = None,
        *,
        allow_fallback: bool = True,
    ) -> bool:
        """送出一則桌面通知。lines 最多顯示 2~3 行，點擊可開啟 launch_url。"""
        if sys.platform != "win32":
            logger.warning("Desktop notification is only supported on Windows.")
            self.last_delivery_receipt = {
                "status": "unsupported",
                "transport": None,
                "platform": sys.platform,
            }
            return False

        body = "\n".join(lines)[:600]
        launch = launch_url or self.cfg.get("notifiers.desktop.launch_url", "http://127.0.0.1:8765")

        toast_xml = (
            f'<toast activationType="protocol" launch="{escape(launch, quote=True)}">'
            f'<visual><binding template="ToastGeneric">'
            f'<text>{escape(title)}</text>'
            f'<text>{escape(body)}</text>'
            f'</binding></visual>'
            f'</toast>'
        )

        script = _TOAST_SCRIPT.replace("__TOAST_XML__", toast_xml).replace("__APP_ID__", _APP_ID)

        try:
            # 寫入暫存 .ps1 再執行，避免長 XML 在命令列被跳脫規則破壞
            with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f:
                f.write(script)
                script_path = f.name

            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
                capture_output=True, text=True, timeout=30
            )
            Path(script_path).unlink(missing_ok=True)

            if result.returncode != 0:
                logger.warning(f"Toast failed ({result.returncode}), falling back to MessageBox: {result.stderr[:200]}")
                self.last_delivery_receipt = {
                    "status": "failed",
                    "transport": "winrt_toast",
                    "platform": sys.platform,
                    "return_code": result.returncode,
                    "stderr": result.stderr[:500],
                    "attempted_at": datetime.now().astimezone().isoformat(),
                }
                return self._fallback_messagebox(title, body) if allow_fallback else False

            logger.info(f"Desktop notification sent: {title}")
            self.last_delivery_receipt = {
                "status": "submitted",
                "transport": "winrt_toast",
                "platform": sys.platform,
                "return_code": result.returncode,
                "attempted_at": datetime.now().astimezone().isoformat(),
            }
            return True
        except Exception as e:
            logger.error(f"Error sending desktop notification: {e}")
            self.last_delivery_receipt = {
                "status": "failed",
                "transport": "winrt_toast",
                "platform": sys.platform,
                "error_type": type(e).__name__,
                "error": str(e)[:500],
                "attempted_at": datetime.now().astimezone().isoformat(),
            }
            return self._fallback_messagebox(title, body) if allow_fallback else False

    def _fallback_messagebox(self, title: str, body: str) -> bool:
        """WinRT 不可用時的降級方案"""
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, body, title, 0x40 | 0x40000)  # MB_ICONINFORMATION | MB_TOPMOST
            self.last_delivery_receipt = {
                "status": "displayed",
                "transport": "message_box",
                "platform": sys.platform,
                "attempted_at": datetime.now().astimezone().isoformat(),
            }
            return True
        except Exception as e:
            logger.error(f"Fallback MessageBox also failed: {e}")
            self.last_delivery_receipt = {
                "status": "failed",
                "transport": "message_box",
                "platform": sys.platform,
                "error_type": type(e).__name__,
                "error": str(e)[:500],
                "attempted_at": datetime.now().astimezone().isoformat(),
            }
            return False


def preview_toast(title: str, lines: List[str]) -> None:
    """``--dry-run`` 的預覽：印出這則通知會長什麼樣，不送出任何東西。"""
    print("\n" + "=" * 50)
    print("🔔 桌面通知預覽 (Dry-run Mode)")
    print("=" * 50)
    print(title)
    for line in lines:
        print(f"  {line}")
    print("=" * 50 + "\n")
