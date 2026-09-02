"""Windows 原生桌面通知 (Toast)

刻意不依賴 winotify / plyer：直接以 PowerShell 呼叫 WinRT ToastNotificationManager，
零額外安裝、零帳號設定，符合「不需要太多設定」的目標。
若 WinRT 不可用（舊版 Windows 或政策限制），自動降級為 MessageBox。
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
from core.time_utils import get_local_now
from core.project_engine import get_active_projects_list, get_open_loops_list, is_bucket_project

logger = logging.getLogger("OmniContext.DesktopNotifier")

def _real_projects(projects: List[dict]) -> List[dict]:
    """濾掉未歸戶的收容桶（判定邏輯集中在 project_engine，避免多份名單各自漂移）"""
    return [p for p in projects if not is_bucket_project(p.get("project_key"))]

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
    def __init__(self):
        self.cfg = get_config()
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

    # ------------------------------------------------------------------
    # 內容組裝
    # ------------------------------------------------------------------
    def send_morning_briefing(self, dry_run: bool = False) -> bool:
        """晨間提醒：昨天做到哪、今天有什麼還沒收尾"""
        now = get_local_now()
        projects = _real_projects([p for p in get_active_projects_list() if p["status"] == "active"])
        open_loops = get_open_loops_list()

        title = f"🌅 OmniContext 晨間簡報 ({now.strftime('%m/%d')})"
        lines = []

        if projects:
            names = "、".join(p["display_name"] for p in projects[:3])
            lines.append(f"進行中：{names}")
        else:
            lines.append("目前沒有活躍中的專案")

        if open_loops:
            lines.append(f"未收尾 {len(open_loops)} 項，最優先：{open_loops[0]['title'][:40]}")
        else:
            lines.append("沒有待收尾事項")

        # 早晨包收據（若排程有跑）：一行帶出 repo 同步／STATUS／Handoff 的產出計數
        try:
            from core.secretary_packs import latest_pack_summary, pack_summary_line

            pack_line = pack_summary_line(latest_pack_summary(now=now))
            if pack_line:
                lines.append(pack_line[:70])
        except Exception:
            pass

        # P5-R4：晨報帶入秘書 top 建議（唯讀；秘書層失敗不阻斷晨報本體）
        try:
            from core.proactive_secretary import briefing_proposals

            secretary = briefing_proposals(limit=2)
            top = secretary.get("proposals") or []
            if top:
                suffix = f"（共 {secretary['total']} 項）" if secretary.get("total", 0) > 1 else ""
                lines.append(f"秘書建議：{str(top[0].get('title') or '')[:36]}{suffix}")
                if top[0].get("why_now"):
                    lines.append(f"為什麼是現在：{str(top[0]['why_now'])[:50]}")
                if secretary.get("advisor_summary"):
                    lines.append(str(secretary["advisor_summary"])[:60])
        except Exception:
            pass

        if dry_run:
            self._preview(title, lines)
            return True
        return self.send(title, lines)

    def send_evening_summary(self, dry_run: bool = False) -> bool:
        """晚間提醒：今天推進了什麼"""
        now = get_local_now()
        today = now.strftime("%Y-%m-%d")
        projects = _real_projects([
            p for p in get_active_projects_list()
            if p["last_activity_at"].startswith(today)
        ])

        title = f"🌙 OmniContext 今日回顧 ({now.strftime('%m/%d')})"
        if projects:
            names = "、".join(p["display_name"] for p in projects[:3])
            lines = [f"今天推進了 {len(projects)} 個專案：{names}", "點此開啟完整日報"]
        else:
            lines = ["今天沒有偵測到專案活動"]

        if dry_run:
            self._preview(title, lines)
            return True
        return self.send(title, lines)

    def send_stagnation_alert(self, dry_run: bool = False) -> bool:
        """停滯提醒：太久沒碰的專案"""
        threshold = self.cfg.get("notifiers.desktop.stagnation_days", 5)
        stagnant = _real_projects([
            p for p in get_active_projects_list()
            if p["status"] in ("idle", "stale") and p["idle_days"] >= threshold
        ])[:3]

        if not stagnant:
            logger.info("No stagnant projects to report.")
            return True

        title = "⚠️ OmniContext 專案停滯提醒"
        lines = [f"{p['display_name']}（已 {p['idle_days']} 天沒動）" for p in stagnant]

        if dry_run:
            self._preview(title, lines)
            return True
        return self.send(title, lines)

    def send_usage_milestone(
        self,
        summary: dict,
        milestone_minutes: int,
        message: str,
        dry_run: bool = False,
    ) -> bool:
        """每日主要介面使用里程碑；message 已由可信度契約產生。"""
        date_text = str(summary.get("date") or get_local_now().strftime("%Y-%m-%d"))
        title = f"🏁 OmniContext 每日里程碑 ({date_text[5:]})"
        lines = [message]
        if summary.get("coverage_status") == "partial":
            lines.append("資料 coverage 為 partial；顯示值是已觀察到的下限。")
        if dry_run:
            self._preview(title, lines)
            return True
        return self.send(
            title,
            lines,
            launch_url=self.cfg.get(
                "notifiers.desktop.launch_url",
                "http://127.0.0.1:8765",
            ),
        )

    @staticmethod
    def _preview(title: str, lines: List[str]):
        print("\n" + "=" * 50)
        print("🔔 桌面通知預覽 (Dry-run Mode)")
        print("=" * 50)
        print(title)
        for line in lines:
            print(f"  {line}")
        print("=" * 50 + "\n")
