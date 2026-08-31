"""Telegram 通知的介面化設定流程（P5-R4b 前置）。

讓使用者在儀表板完成「貼 bot token → 偵測 chat id → 即時連線測試 →
儲存啟用」，不必手動編輯 config.yaml 或設環境變數。安全邊界沿用
既有慣例（GitHub PAT 連線流程與 LLM key 偵測）：

- **secret 永不回流瀏覽器**：所有 receipt 只含布林、來源標籤與 bot
  顯示名稱；`bot_token`／`chat_id` 已在 `security.redact_config` 的
  遮蔽清單內，GET /api/v1/config 回傳 ``***REDACTED***``，儲存時
  `merge_redacted_config` 保留原值。唯一例外是 chat id 偵測候選清單
  ——那是使用者主動向「自己的 bot」查詢自己的對話，選定後前端需要
  這個值來回填儲存。
- **驗證通過才儲存**：connect 流程先 `getMe`（驗 token）、有 chat id
  再發一則固定內容的測試訊息，全部成功才寫入 config.yaml。
- **環境變數優先且不複製**：token／chat id 若來自環境變數，僅回報
  來源，絕不把 env 值複製進 config 檔。
- 所有對 Telegram API 的呼叫走 HTTPS、固定 timeout；HTTP transport
  可注入，contract tests 不需真實網路與真實 token。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from core.config import get_config

logger = logging.getLogger("OmniContext.TelegramSetup")

TELEGRAM_API_BASE = "https://api.telegram.org"
API_TIMEOUT_SECONDS = 10
TEST_MESSAGE_TEXT = (
    "✅ OmniContext 測試訊息：Telegram 通知通道已連通。"
    "（此訊息由您在儀表板按「測試連線」觸發，不含任何工作內容）"
)
_TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

SETUP_CLAIM_BOUNDARY = (
    "此流程只驗證 bot token 與 chat id 可用並保存於本機 config.yaml；"
    "secret 不回傳瀏覽器、不進 log；環境變數存在時優先使用且不複製進檔案。"
)

# transport(url, payload, timeout) -> (status_code, parsed_json_dict)
Transport = Callable[[str, dict[str, Any], int], tuple[int, dict[str, Any]]]


def _default_transport(url: str, payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any]]:
    import requests

    response = requests.post(url, json=payload, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def _call_api(
    bot_token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    transport: Optional[Transport] = None,
) -> tuple[int, dict[str, Any]]:
    """呼叫 Bot API；URL 含 token，因此絕不記 log、絕不放進回傳 receipt。"""
    transport = transport or _default_transport
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    return transport(url, payload or {}, API_TIMEOUT_SECONDS)


def _resolve_credential(
    provided: Optional[str],
    env_key_cfg: str,
    env_default: str,
    config_key: str,
    cfg: Any,
) -> tuple[Optional[str], str]:
    """回傳 (值, 來源)；來源 ∈ provided / env / config / missing。"""
    import os

    if provided and str(provided).strip():
        return str(provided).strip(), "provided"
    env_name = str(cfg.get(env_key_cfg, env_default) or env_default)
    env_value = os.environ.get(env_name)
    if env_value and env_value.strip():
        return env_value.strip(), "env"
    config_value = cfg.get(config_key)
    if config_value and str(config_value).strip():
        return str(config_value).strip(), "config"
    return None, "missing"


def _resolve_bot_token(cfg: Any, provided: Optional[str] = None) -> tuple[Optional[str], str]:
    return _resolve_credential(
        provided,
        "notifiers.telegram.bot_token_env",
        "TELEGRAM_BOT_TOKEN",
        "notifiers.telegram.bot_token",
        cfg,
    )


def _resolve_chat_id(cfg: Any, provided: Optional[str] = None) -> tuple[Optional[str], str]:
    return _resolve_credential(
        provided,
        "notifiers.telegram.chat_id_env",
        "TELEGRAM_CHAT_ID",
        "notifiers.telegram.chat_id",
        cfg,
    )


def telegram_status(cfg: Any | None = None) -> dict[str, Any]:
    """設定現況（無 secret 值）：讓 UI 呈現 DETECTED/MISSING 與來源。"""
    cfg = cfg or get_config()
    token, token_source = _resolve_bot_token(cfg)
    chat_id, chat_source = _resolve_chat_id(cfg)
    return {
        "enabled": bool(cfg.get("notifiers.telegram.enabled", False)),
        "token_configured": token is not None,
        "token_source": token_source,
        "chat_id_configured": chat_id is not None,
        "chat_id_source": chat_source,
        "morning_briefing_time": str(cfg.get("notifiers.telegram.morning_briefing_time", "09:00")),
        "evening_summary_time": str(cfg.get("notifiers.telegram.evening_summary_time", "23:30")),
        "secret_boundary": "status_only_no_secret_values",
    }


def _error_receipt(error_code: str, hint: str, **extra: Any) -> dict[str, Any]:
    receipt = {"ok": False, "error_code": error_code, "hint": hint}
    receipt.update(extra)
    return receipt


def _classify_api_failure(status_code: int, body: dict[str, Any], step: str) -> dict[str, Any]:
    description = str(body.get("description") or "")[:200]
    if status_code == 401 or status_code == 404:
        return _error_receipt(
            "invalid_token",
            "Telegram 拒絕此 bot token；請回 @BotFather 確認 token（/mybots → API Token）後重貼",
            step=step,
        )
    if status_code == 400 and "chat not found" in description.lower():
        return _error_receipt(
            "chat_not_found",
            "bot 找不到這個 chat：請先在 Telegram 對您的 bot 送出任意訊息（例如 /start），再重新偵測 chat id",
            step=step,
        )
    if status_code == 429:
        return _error_receipt(
            "rate_limited", "Telegram API 流量限制，請稍後再試", step=step
        )
    return _error_receipt(
        "telegram_api_error",
        f"Telegram API 回應 {status_code}" + (f"：{description}" if description else ""),
        step=step,
    )


def test_telegram_connection(
    *,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    send_test_message: bool = True,
    cfg: Any | None = None,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    """即時連線測試：getMe 驗 token；有 chat id 再實發一則測試訊息。

    receipt 只含結果與 bot 顯示名稱，不含 token／chat id 值。
    """
    cfg = cfg or get_config()
    token, token_source = _resolve_bot_token(cfg, bot_token)
    chat, chat_source = _resolve_chat_id(cfg, chat_id)
    if token is None:
        return _error_receipt(
            "token_missing",
            "尚未提供 bot token：貼上 @BotFather 給的 token，或設定環境變數 TELEGRAM_BOT_TOKEN",
            step="resolve",
            token_source=token_source,
        )

    try:
        status_code, body = _call_api(token, "getMe", transport=transport)
    except Exception as exc:  # noqa: BLE001 — 網路層失敗如實回報，不外洩內部細節
        logger.warning("Telegram getMe failed: %s", type(exc).__name__)
        return _error_receipt(
            "network_unreachable",
            "無法連線 api.telegram.org：請確認本機網路／防火牆／Proxy 設定",
            step="getMe",
        )
    if status_code != 200 or not body.get("ok"):
        return _classify_api_failure(status_code, body, "getMe")

    bot_info = body.get("result") or {}
    receipt: dict[str, Any] = {
        "ok": True,
        "bot_username": str(bot_info.get("username") or ""),
        "bot_name": str(bot_info.get("first_name") or ""),
        "token_source": token_source,
        "chat_id_source": chat_source,
        "message_sent": None,
        "claim_boundary": SETUP_CLAIM_BOUNDARY,
    }

    if chat is None:
        receipt["hint"] = (
            "token 有效；尚未設定 chat id——先在 Telegram 對 bot 送出訊息，再按「偵測 CHAT ID」"
        )
        return receipt

    if send_test_message:
        try:
            status_code, body = _call_api(
                token,
                "sendMessage",
                {"chat_id": chat, "text": TEST_MESSAGE_TEXT, "disable_web_page_preview": True},
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram sendMessage failed: %s", type(exc).__name__)
            failure = _error_receipt(
                "network_unreachable",
                "getMe 成功但發送測試訊息時網路中斷，請重試",
                step="sendMessage",
            )
            failure["bot_username"] = receipt["bot_username"]
            return failure
        if status_code != 200 or not body.get("ok"):
            failure = _classify_api_failure(status_code, body, "sendMessage")
            failure["bot_username"] = receipt["bot_username"]
            return failure
        receipt["message_sent"] = True
    return receipt


def detect_telegram_chat_id(
    *,
    bot_token: Optional[str] = None,
    cfg: Any | None = None,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    """由 getUpdates 列出最近對 bot 傳過訊息的 chat 候選（id＋顯示名稱）。

    候選 chat id 是使用者主動查詢自己 bot 的結果，供前端選定後回填儲存；
    除此之外不回傳訊息內容或任何 secret。
    """
    cfg = cfg or get_config()
    token, _source = _resolve_bot_token(cfg, bot_token)
    if token is None:
        return _error_receipt(
            "token_missing", "請先貼上 bot token 再偵測 chat id", step="resolve"
        )
    try:
        status_code, body = _call_api(token, "getUpdates", {"limit": 50}, transport=transport)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram getUpdates failed: %s", type(exc).__name__)
        return _error_receipt(
            "network_unreachable",
            "無法連線 api.telegram.org：請確認本機網路／防火牆／Proxy 設定",
            step="getUpdates",
        )
    if status_code != 200 or not body.get("ok"):
        return _classify_api_failure(status_code, body, "getUpdates")

    candidates: dict[str, dict[str, Any]] = {}
    for update in body.get("result") or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        title = str(
            chat.get("title")
            or " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            )
            or chat.get("username")
            or "unknown"
        )
        # 同一 chat 多筆 update 只留第一筆（避免較不完整的名稱覆寫掉完整版）
        candidates.setdefault(
            str(chat_id),
            {
                "chat_id": str(chat_id),
                "chat_type": str(chat.get("type") or "unknown"),
                "display_name": title[:80],
            },
        )
    result: dict[str, Any] = {"ok": True, "candidates": list(candidates.values())}
    if not candidates:
        result["hint"] = (
            "沒有偵測到任何對話：請先在 Telegram 搜尋您的 bot、送出 /start 或任意訊息，再按一次偵測"
        )
    return result


def _validate_time(value: Optional[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if _TIME_PATTERN.match(text) else fallback


def save_telegram_settings(
    *,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    enabled: bool = True,
    morning_briefing_time: Optional[str] = None,
    evening_summary_time: Optional[str] = None,
    cfg: Any | None = None,
    transport: Optional[Transport] = None,
    config_writer: Optional[Callable[[Any], None]] = None,
) -> dict[str, Any]:
    """connect 流程：先即時驗證（getMe＋測試訊息），全部通過才寫 config。

    - 由 UI 提供的 token／chat id 才寫入 config；來自環境變數的值只
      沿用、不複製進檔案。
    - 驗證失敗 → 原樣回傳失敗 receipt，config 完全不動（fail-closed）。
    """
    cfg = cfg or get_config()
    receipt = test_telegram_connection(
        bot_token=bot_token,
        chat_id=chat_id,
        send_test_message=True,
        cfg=cfg,
        transport=transport,
    )
    if not receipt.get("ok"):
        receipt["saved"] = False
        return receipt
    if receipt.get("message_sent") is not True:
        receipt.update(
            _error_receipt(
                "chat_id_missing",
                "需要 chat id 才能啟用推播：先對 bot 送訊息，再按「偵測 CHAT ID」選擇對話",
                step="save",
            )
        )
        receipt["saved"] = False
        return receipt

    telegram_cfg = cfg.data.setdefault("notifiers", {}).setdefault("telegram", {})
    telegram_cfg["enabled"] = bool(enabled)
    if bot_token and str(bot_token).strip():
        telegram_cfg["bot_token"] = str(bot_token).strip()
    if chat_id and str(chat_id).strip():
        telegram_cfg["chat_id"] = str(chat_id).strip()
    telegram_cfg["morning_briefing_time"] = _validate_time(
        morning_briefing_time, str(telegram_cfg.get("morning_briefing_time", "09:00"))
    )
    telegram_cfg["evening_summary_time"] = _validate_time(
        evening_summary_time, str(telegram_cfg.get("evening_summary_time", "23:30"))
    )

    if config_writer is not None:
        config_writer(cfg)
    else:
        _write_config(cfg)
    receipt["saved"] = True
    receipt["enabled"] = bool(enabled)
    return receipt


def disconnect_telegram(
    *,
    cfg: Any | None = None,
    config_writer: Optional[Callable[[Any], None]] = None,
) -> dict[str, Any]:
    """停用並清除 config 內的 Telegram secret（環境變數不受影響）。"""
    cfg = cfg or get_config()
    telegram_cfg = cfg.data.setdefault("notifiers", {}).setdefault("telegram", {})
    telegram_cfg["enabled"] = False
    telegram_cfg["bot_token"] = ""
    telegram_cfg["chat_id"] = ""
    if config_writer is not None:
        config_writer(cfg)
    else:
        _write_config(cfg)
    return {
        "ok": True,
        "enabled": False,
        "hint": "已停用並清除 config 內的 token／chat id；環境變數（若有）不受影響",
    }


def _write_config(cfg: Any) -> None:
    import yaml

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.config_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg.data, handle, allow_unicode=True, sort_keys=False)
