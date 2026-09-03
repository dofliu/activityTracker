"""推播通道 adapter（ADR-014）：同一份訊息可送 Telegram、LINE 或兩者。

每個 adapter 只負責「把 :class:`notifiers.messages.Message` 變成該平台的
請求」，並回傳非敏感 receipt。能力差異用旗標明示，呼叫端不必知道平台細節：

=================  ========  ====  ==========================================
能力                Telegram  LINE  說明
=================  ========  ====  ==========================================
推播                ✅        ✅    outbound HTTPS
接收訊息            ✅        ❌    LINE 只有 webhook，需公開入口（見 ADR-014）
按鈕批准            ✅        ❌    需先能接收；LINE 的 postback 同樣要 webhook
刪除使用者訊息      ✅        ❌    LINE 沒有這個 API
富文字              HTML      純文字
=================  ========  ====  ==========================================

契約：任一通道失敗都不影響其他通道（各自 try/except，receipt 如實記錄）；
receipt 永不含 token、收件 id 或訊息全文。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.config import get_config
from notifiers.messages import Message, render_plain, render_telegram_html

logger = logging.getLogger("OmniContext.Channels")

TELEGRAM_CHUNK_CHARS = 3500
LINE_CHUNK_CHARS = 4800
LINE_MAX_MESSAGES_PER_PUSH = 5


class ChannelAdapter:
    """通道共通介面；``send`` 回傳 ``{"channel", "sent", ...}``。"""

    name = "channel"
    supports_receive = False
    supports_buttons = False
    supports_delete = False
    rich_text = "plain"

    def render(self, message: Message) -> str:
        return render_plain(message)

    def send(self, message: Message) -> dict[str, Any]:
        return self.send_text(self.render(message))

    def send_text(self, text: str) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def capabilities(self) -> dict[str, Any]:
        return {
            "channel": self.name,
            "receive": self.supports_receive,
            "buttons": self.supports_buttons,
            "delete_message": self.supports_delete,
            "rich_text": self.rich_text,
        }


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class TelegramChannel(ChannelAdapter):
    name = "telegram"
    supports_receive = True
    supports_buttons = True
    supports_delete = True
    rich_text = "html"

    def __init__(self, token: str, chat: str, *, transport: Any | None = None):
        self._token = token
        self._chat = chat
        self._transport = transport

    def render(self, message: Message) -> str:
        return render_telegram_html(message)

    def send_text(self, text: str, *, parse_mode: str | None = "HTML") -> dict[str, Any]:
        from notifiers.telegram_setup import _call_api

        sent = 0
        for chunk in _chunks(text, TELEGRAM_CHUNK_CHARS):
            payload: dict[str, Any] = {
                "chat_id": self._chat,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                status_code, body = _call_api(self._token, "sendMessage", payload, transport=self._transport)
            except Exception as exc:  # noqa: BLE001 — 網路層失敗只記型別
                logger.warning("Telegram send failed: %s", type(exc).__name__)
                return {"channel": self.name, "sent": False, "error": type(exc).__name__, "parts_sent": sent}
            if status_code != 200 or not body.get("ok"):
                description = str(body.get("description") or "").lower()
                # HTML 解析失敗（內容含未預期標記）時降級為純文字重送一次
                if parse_mode and "can't parse entities" in description:
                    logger.warning("Telegram rejected HTML; retrying as plain text.")
                    return self.send_text(text, parse_mode=None)
                return {
                    "channel": self.name,
                    "sent": False,
                    "error": f"http_{status_code}",
                    "parts_sent": sent,
                }
            sent += 1
        return {"channel": self.name, "sent": True, "parts_sent": sent}


class LineChannel(ChannelAdapter):
    name = "line"
    supports_receive = False  # LINE 只有 webhook；見 ADR-014
    supports_buttons = False
    supports_delete = False
    rich_text = "plain"

    def __init__(self, access_token: str, to: str, *, transport: Any | None = None):
        self._token = access_token
        self._to = to
        self._transport = transport

    def send_text(self, text: str) -> dict[str, Any]:
        from notifiers.line_setup import _call_api

        chunks = _chunks(text, LINE_CHUNK_CHARS)
        sent = 0
        # LINE 單次 push 最多 5 則訊息；超長內容分批 push。
        for start in range(0, len(chunks), LINE_MAX_MESSAGES_PER_PUSH):
            batch = chunks[start : start + LINE_MAX_MESSAGES_PER_PUSH]
            payload = {"to": self._to, "messages": [{"type": "text", "text": part} for part in batch]}
            try:
                status_code, body = _call_api(self._token, "message/push", payload, transport=self._transport)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LINE push failed: %s", type(exc).__name__)
                return {"channel": self.name, "sent": False, "error": type(exc).__name__, "parts_sent": sent}
            if status_code != 200:
                error = f"http_{status_code}"
                if status_code == 429:
                    error = "quota_or_rate_limited"
                return {"channel": self.name, "sent": False, "error": error, "parts_sent": sent}
            sent += len(batch)
        return {"channel": self.name, "sent": True, "parts_sent": sent}


# ---------------------------------------------------------------- 解析設定


def telegram_channel(cfg: Any | None = None, *, transport: Any | None = None) -> TelegramChannel | None:
    """設定完整且啟用時回傳 adapter，否則 None。"""
    from notifiers.telegram_setup import _resolve_bot_token, _resolve_chat_id

    cfg = cfg or get_config()
    if not bool(cfg.get("notifiers.telegram.enabled", False)):
        return None
    token, _ = _resolve_bot_token(cfg)
    chat, _ = _resolve_chat_id(cfg)
    if not token or not chat:
        return None
    return TelegramChannel(token, str(chat), transport=transport)


def line_channel(cfg: Any | None = None, *, transport: Any | None = None) -> LineChannel | None:
    from notifiers.line_setup import _resolve_access_token, _resolve_to_id

    cfg = cfg or get_config()
    if not bool(cfg.get("notifiers.line.enabled", False)):
        return None
    token, _ = _resolve_access_token(cfg)
    to_id, _ = _resolve_to_id(cfg)
    if not token or not to_id:
        return None
    return LineChannel(token, str(to_id), transport=transport)


def enabled_push_channels(
    cfg: Any | None = None,
    *,
    telegram_transport: Any | None = None,
    line_transport: Any | None = None,
) -> list[ChannelAdapter]:
    """目前可推播的通道；兩個都沒設定就回空清單（呼叫端據此跳過推播）。"""
    cfg = cfg or get_config()
    channels: list[ChannelAdapter] = []
    telegram = telegram_channel(cfg, transport=telegram_transport)
    if telegram:
        channels.append(telegram)
    line = line_channel(cfg, transport=line_transport)
    if line:
        channels.append(line)
    return channels


def channels_status(cfg: Any | None = None) -> dict[str, Any]:
    """UI／API 用的通道總覽；不含任何 secret 值。"""
    cfg = cfg or get_config()
    from notifiers.line_setup import line_status
    from notifiers.telegram_setup import telegram_status

    telegram = telegram_status(cfg)
    line = line_status(cfg)
    ready = [channel.name for channel in enabled_push_channels(cfg)]
    return {
        "push_ready": ready,
        "channels": {
            "telegram": {
                **{k: v for k, v in telegram.items() if k != "secret_boundary"},
                **TelegramChannel("x", "x").capabilities(),
            },
            "line": {
                **{k: v for k, v in line.items() if k != "secret_boundary"},
                **LineChannel("x", "x").capabilities(),
            },
        },
        "claim_boundary": (
            "只回報開關與是否已設定，不含 token 或收件 id。LINE 只能推播："
            "接收訊息需要公開 webhook，本專案維持 loopback-only（見 ADR-014）。"
        ),
    }
