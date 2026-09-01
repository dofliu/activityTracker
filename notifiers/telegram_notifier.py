import os
import logging
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime

from core.config import get_config
from core.database import get_db
from core.models import DailySummary, ProjectState, OpenLoop
from core.time_utils import get_local_now
from core.project_engine import get_active_projects_list, get_open_loops_list

logger = logging.getLogger("OmniContext.TelegramNotifier")


class TelegramNotifier:
    def __init__(self):
        self.cfg = get_config()

    def _get_credentials(self) -> tuple[Optional[str], Optional[str]]:
        """取得 Telegram Bot Token 與 Chat ID (支援 config.yaml 或環境變數)"""
        token_env = self.cfg.get("notifiers.telegram.bot_token_env", "TELEGRAM_BOT_TOKEN")
        chat_id_env = self.cfg.get("notifiers.telegram.chat_id_env", "TELEGRAM_CHAT_ID")

        bot_token = os.environ.get(token_env) or self.cfg.get("notifiers.telegram.bot_token")
        chat_id = os.environ.get(chat_id_env) or self.cfg.get("notifiers.telegram.chat_id")

        return bot_token, chat_id

    def is_enabled(self) -> bool:
        enabled = self.cfg.get("notifiers.telegram.enabled", False)
        bot_token, chat_id = self._get_credentials()
        return bool(enabled and bot_token and chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """發送訊息至 Telegram"""
        bot_token, chat_id = self._get_credentials()
        if not bot_token or not chat_id:
            logger.warning("Telegram bot_token or chat_id not configured. Message skipped.")
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # Telegram 單則訊息上限 4096 字元，若過長進行分段
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]

        success = True
        for chunk in chunks:
            try:
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True
                }
                res = requests.post(url, json=payload, timeout=15)
                if not res.ok:
                    # 若 HTML 格式解析錯誤，降級為純文字重送
                    if "can't parse entities" in res.text.lower():
                        payload.pop("parse_mode")
                        requests.post(url, json=payload, timeout=15)
                    else:
                        logger.error(f"Failed to send Telegram message: {res.text}")
                        success = False
            except Exception as e:
                logger.error(f"Error sending Telegram message: {e}")
                success = False

        return success

    def send_daily_summary(self, date_str: str) -> bool:
        """發送指定日期的 AI 全景工作日報"""
        db = get_db()
        with db.session_scope() as session:
            summary = session.query(DailySummary).filter_by(date_str=date_str).first()
            if not summary:
                logger.warning(f"No summary found for {date_str} to send via Telegram.")
                return False

            raw_md = summary.raw_markdown

            # 轉化為精簡版 Telegram 推播文本
            msg = f"<b>📅 OmniContext 每日全景工作日報 ({date_str})</b>\n\n{raw_md[:3800]}"
            return self.send_message(msg, parse_mode="HTML")

    def send_morning_briefing(self) -> bool:
        """發送每日晨報：活躍專案與 Open Loops 提醒"""
        now = get_local_now()
        projects = get_active_projects_list()
        open_loops = get_open_loops_list()

        active_projs = [p for p in projects if p["status"] == "active"][:5]

        lines = [
            f"<b>🌅 OmniContext 晨間簡報 ({now.strftime('%Y-%m-%d')})</b>",
            "",
            "<b>🔥 今日重點活躍專案：</b>"
        ]

        if active_projs:
            for p in active_projs:
                lines.append(f"• <b>{p['display_name']}</b>: {p['last_action_summary']}")
        else:
            lines.append("• <i>(目前尚無高頻專案)</i>")

        lines.extend([
            "",
            f"<b>📌 待跟進未結事項 ({len(open_loops)} 項)：</b>"
        ])

        if open_loops:
            for ol in open_loops[:6]:
                lines.append(f"• [ ] <b>[{ol['project_key']}]</b> {ol['title']}")
        else:
            lines.append("• <i>(目前無待辦未結事項)</i>")

        lines.append("\n👉 <i>祝今天研究與開發順利！</i>")
        return self.send_message("\n".join(lines), parse_mode="HTML")

    def send_evening_handoff(self) -> bool:
        """P5-R4b 晚間交接（唯讀推播）：今日推進的專案與未結事項盤點。

        只推觀測到的事實，不歸檔、不改任何資料；待判斷建議（含批准按鈕）
        由 scheduler 以 telegram_approvals 另行推送。
        """
        now = get_local_now()
        today = now.strftime("%Y-%m-%d")
        projects = [
            p for p in get_active_projects_list()
            if str(p.get("last_activity_at", "")).startswith(today)
        ]
        open_loops = get_open_loops_list()

        lines = [f"<b>🌙 OmniContext 晚間交接 ({now.strftime('%Y-%m-%d')})</b>", ""]
        if projects:
            lines.append(f"<b>今日推進 {len(projects)} 個專案：</b>")
            for p in projects[:6]:
                lines.append(f"• <b>{p['display_name']}</b>: {p['last_action_summary']}")
        else:
            lines.append("<i>今天沒有偵測到專案活動。</i>")
        lines.extend(["", f"<b>📌 未結事項盤點（{len(open_loops)} 項待跟進）：</b>"])
        if open_loops:
            for ol in open_loops[:6]:
                lines.append(f"• [ ] <b>[{ol['project_key']}]</b> {ol['title']}")
        else:
            lines.append("• <i>(目前無待辦未結事項)</i>")
        lines.append("\n<i>此為唯讀盤點；明早晨報會再附上待判斷建議。</i>")
        return self.send_message("\n".join(lines), parse_mode="HTML")

    def send_stagnation_alert(self) -> bool:
        """發送停滯專案警示 (閒置超過 3 天的專案)"""
        projects = get_active_projects_list()
        stagnant = [p for p in projects if p["status"] in ["idle", "stale"] and p["idle_days"] >= 3][:4]

        if not stagnant:
            return True

        lines = [
            "<b>⚠️ OmniContext 專案停滯提醒</b>",
            "以下專案已連續數日未有新活動：",
            ""
        ]
        for p in stagnant:
            lines.append(f"• <b>{p['display_name']}</b> (已閒置 {p['idle_days']} 天)\n  └─ 上次動態: {p['last_action_summary']}")

        lines.append("\n💡 <i>是否需要安排下一步推進？</i>")
        return self.send_message("\n".join(lines), parse_mode="HTML")
