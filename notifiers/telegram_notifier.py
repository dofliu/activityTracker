import os
import logging
from typing import Optional

from core.config import get_config

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
        """發送純文字／HTML 至 Telegram（分段、HTML 解析失敗自動降級為純文字）。"""
        from notifiers.channels import telegram_channel

        channel = telegram_channel(self.cfg)
        if channel is None:
            logger.warning("Telegram is disabled or not configured. Message skipped.")
            return False
        return bool(channel.send_text(text, parse_mode=parse_mode or None).get("sent"))

    # ------------------------------------------------------------------
    # 內容組裝已移到 notifiers/messages.py（通道中立），傳送走 notifiers/channels.py。
    # 這些方法保留為相容外殼：只推 Telegram（維持既有語意）；要一次送所有啟用
    # 的通道請用 notifiers.secretary_push.push_*（ADR-014）。
    # ------------------------------------------------------------------
    def _push_telegram_only(self, message) -> bool:
        from notifiers.channels import telegram_channel
        from notifiers.secretary_push import push_message

        channel = telegram_channel(self.cfg)
        receipt = push_message(
            message, kind="telegram_only", cfg=self.cfg, channels=[channel] if channel else []
        )
        return receipt["sent"] > 0

    def send_daily_summary(self, date_str: str) -> bool:
        from notifiers.messages import build_daily_summary

        return self._push_telegram_only(build_daily_summary(date_str))

    def send_morning_briefing(self) -> bool:
        from notifiers.messages import build_morning_briefing

        return self._push_telegram_only(build_morning_briefing())

    def send_evening_handoff(self) -> bool:
        """晚間交接（唯讀推播）：只推觀測到的事實，不歸檔、不改任何資料。"""
        from notifiers.messages import build_evening_handoff

        return self._push_telegram_only(build_evening_handoff())

    def send_stagnation_alert(self) -> bool:
        from notifiers.messages import build_stagnation_alert

        return self._push_telegram_only(build_stagnation_alert())
