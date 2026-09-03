"""LINE Messaging API 設定與傳送（ADR-014）。

與 `telegram_setup` 對稱：憑證解析（環境變數優先、不複製進檔案）、即時連線
測試（`/v2/bot/info` 驗 token ＋ 實發一則測試訊息）、驗證通過才寫 config。

**與 Telegram 的關鍵差異（決定了本專案只用 LINE 做推播）**：

- LINE Messaging API **沒有輪詢介面**（沒有 getUpdates 這種東西）。要接收
  使用者的訊息只能由 LINE 平台 webhook POST 到一個公開 HTTPS 網址，那需要
  在本機開對外入口，會動到 ADR-001 的 loopback-only 邊界。因此本模組只做
  **outbound push**：晨報／晚報／日報／停滯提醒。提問與批准仍走 Telegram。
- LINE 的純文字訊息**不支援 HTML／Markdown**，所以內容一律用
  `notifiers.messages.render_plain` 呈現。
- LINE 官方帳號的免費方案有**每月推播訊息上限**（依地區方案不同）；本專案
  預設一天最多幾則（晨報／晚報），但仍請自行確認方案額度。
- `to` 是 LINE 的 userId／groupId／roomId，**不是** LINE ID（@xxxx）。取得
  方式見 USAGE：加好友後由 webhook 或 LINE Developers Console 取得 userId。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from core.config import get_config

logger = logging.getLogger("OmniContext.LineSetup")

LINE_API_BASE = "https://api.line.me/v2/bot"
API_TIMEOUT_SECONDS = 15
TEST_MESSAGE_TEXT = "OmniContext 連線測試：這是一則固定內容的測試訊息，收到表示推播通道已就緒。"
MAX_TEXT_CHARS = 4800  # LINE 單則文字上限 5000，留餘裕

SETUP_CLAIM_BOUNDARY = (
    "測試會實際呼叫 LINE API 驗證 channel access token 並實發一則測試訊息；"
    "token 與收件 id 只存本機，receipt 永不含 secret 值。"
)

# (url, payload, headers, timeout) -> (status_code, body)
Transport = Callable[[str, dict[str, Any], dict[str, str], int], tuple[int, dict[str, Any]]]


def _default_transport(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int
) -> tuple[int, dict[str, Any]]:
    import requests

    if payload:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    else:
        response = requests.get(url, headers=headers, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def _call_api(
    access_token: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    transport: Optional[Transport] = None,
    timeout: int = API_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    """呼叫 LINE Bot API；token 只出現在 Authorization header，絕不進 URL 或 log。"""
    transport = transport or _default_transport
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    return transport(f"{LINE_API_BASE}/{path}", payload or {}, headers, timeout)


def _resolve_credential(
    provided: Optional[str], env_key_cfg: str, env_default: str, config_key: str, cfg: Any
) -> tuple[Optional[str], str]:
    """回傳 (值, 來源)；來源 ∈ provided / env / config / missing。"""
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


def _resolve_access_token(cfg: Any, provided: Optional[str] = None) -> tuple[Optional[str], str]:
    return _resolve_credential(
        provided,
        "notifiers.line.access_token_env",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "notifiers.line.access_token",
        cfg,
    )


def _resolve_to_id(cfg: Any, provided: Optional[str] = None) -> tuple[Optional[str], str]:
    return _resolve_credential(
        provided, "notifiers.line.to_env", "LINE_TO_ID", "notifiers.line.to", cfg
    )


def line_status(cfg: Any | None = None) -> dict[str, Any]:
    """設定現況（無 secret 值）。"""
    cfg = cfg or get_config()
    token, token_source = _resolve_access_token(cfg)
    to_id, to_source = _resolve_to_id(cfg)
    return {
        "enabled": bool(cfg.get("notifiers.line.enabled", False)),
        "token_configured": token is not None,
        "token_source": token_source,
        "to_configured": to_id is not None,
        "to_source": to_source,
        "push_only": True,
        "push_only_reason": "LINE Messaging API 沒有輪詢介面；接收訊息需要公開 webhook，本專案維持 loopback-only，因此 LINE 只做推播。",
        "secret_boundary": "status_only_no_secret_values",
    }


def _error_receipt(error_code: str, hint: str, **extra: Any) -> dict[str, Any]:
    receipt = {"ok": False, "error_code": error_code, "hint": hint}
    receipt.update(extra)
    return receipt


def _classify_api_failure(status_code: int, body: dict[str, Any], step: str) -> dict[str, Any]:
    message = str(body.get("message") or "")[:200]
    if status_code in (401, 403):
        return _error_receipt(
            "invalid_token",
            "LINE 拒絕此 channel access token；請回 LINE Developers Console → Messaging API → Channel access token 重新發行並貼上",
            step=step,
        )
    if status_code == 400:
        return _error_receipt(
            "invalid_request",
            "LINE 拒絕這次請求" + (f"：{message}" if message else "")
            + "。最常見原因是收件 id 不對——需要 userId（U 開頭的長字串），不是 LINE ID（@xxxx）",
            step=step,
        )
    if status_code == 429:
        return _error_receipt(
            "rate_limited",
            "已達 LINE 推播額度或流量限制（免費方案每月推播則數有限）；請稍後再試或確認方案額度",
            step=step,
        )
    return _error_receipt(
        "line_api_error",
        f"LINE API 回應 {status_code}" + (f"：{message}" if message else ""),
        step=step,
    )


def test_line_connection(
    *,
    access_token: Optional[str] = None,
    to: Optional[str] = None,
    send_test_message: bool = True,
    cfg: Any | None = None,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    """即時連線測試：先 /info 驗 token，有收件 id 再實發一則測試訊息。"""
    cfg = cfg or get_config()
    token, token_source = _resolve_access_token(cfg, access_token)
    to_id, to_source = _resolve_to_id(cfg, to)
    if token is None:
        return _error_receipt(
            "token_missing",
            "尚未提供 channel access token：貼上 LINE Developers Console 發行的長期 token，或設定環境變數 LINE_CHANNEL_ACCESS_TOKEN",
            step="resolve",
            token_source=token_source,
        )

    try:
        status_code, body = _call_api(token, "info", transport=transport)
    except Exception as exc:  # noqa: BLE001 — 網路層失敗如實回報
        logger.warning("LINE /info failed: %s", type(exc).__name__)
        return _error_receipt(
            "network_unreachable",
            "無法連線 api.line.me：請確認本機網路／防火牆／Proxy 設定",
            step="info",
        )
    if status_code != 200:
        return _classify_api_failure(status_code, body, "info")

    receipt: dict[str, Any] = {
        "ok": True,
        "bot_basic_id": str(body.get("basicId") or ""),
        "bot_display_name": str(body.get("displayName") or ""),
        "token_source": token_source,
        "to_source": to_source,
        "message_sent": None,
        "push_only": True,
        "claim_boundary": SETUP_CLAIM_BOUNDARY,
    }

    if to_id is None:
        receipt["hint"] = (
            "token 有效；尚未設定收件 id——請先把官方帳號加為好友，再從 LINE Developers Console"
            "（Basic settings → Your user ID）複製 userId 貼上"
        )
        return receipt

    if send_test_message:
        try:
            status_code, body = _call_api(
                token,
                "message/push",
                {"to": to_id, "messages": [{"type": "text", "text": TEST_MESSAGE_TEXT}]},
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LINE push failed: %s", type(exc).__name__)
            failure = _error_receipt(
                "network_unreachable", "token 驗證成功但發送測試訊息時網路中斷，請重試", step="push"
            )
            failure["bot_basic_id"] = receipt["bot_basic_id"]
            return failure
        if status_code != 200:
            failure = _classify_api_failure(status_code, body, "push")
            failure["bot_basic_id"] = receipt["bot_basic_id"]
            return failure
        receipt["message_sent"] = True
    return receipt


def _write_config(cfg: Any) -> None:
    import yaml

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.config_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg.data, handle, allow_unicode=True, sort_keys=False)


def save_line_settings(
    *,
    access_token: Optional[str] = None,
    to: Optional[str] = None,
    enabled: bool = True,
    cfg: Any | None = None,
    transport: Optional[Transport] = None,
    config_writer: Optional[Callable[[Any], None]] = None,
) -> dict[str, Any]:
    """connect 流程：先即時驗證（/info＋測試訊息），全部通過才寫 config。"""
    cfg = cfg or get_config()
    receipt = test_line_connection(
        access_token=access_token, to=to, send_test_message=True, cfg=cfg, transport=transport
    )
    if not receipt.get("ok"):
        receipt["saved"] = False
        return receipt
    if receipt.get("message_sent") is not True:
        receipt.update(
            _error_receipt(
                "to_missing",
                "需要收件 id 才能啟用推播：加好友後從 LINE Developers Console 複製 userId",
                step="save",
            )
        )
        receipt["saved"] = False
        return receipt

    line_cfg = cfg.data.setdefault("notifiers", {}).setdefault("line", {})
    line_cfg["enabled"] = bool(enabled)
    if access_token and str(access_token).strip():
        line_cfg["access_token"] = str(access_token).strip()
    if to and str(to).strip():
        line_cfg["to"] = str(to).strip()

    (config_writer or _write_config)(cfg)
    receipt["saved"] = True
    receipt["enabled"] = bool(enabled)
    return receipt


def disconnect_line(
    *, cfg: Any | None = None, config_writer: Optional[Callable[[Any], None]] = None
) -> dict[str, Any]:
    """停用並清除 config 內的 LINE secret（環境變數不受影響）。"""
    cfg = cfg or get_config()
    line_cfg = cfg.data.setdefault("notifiers", {}).setdefault("line", {})
    line_cfg["enabled"] = False
    line_cfg["access_token"] = ""
    line_cfg["to"] = ""
    (config_writer or _write_config)(cfg)
    return {
        "ok": True,
        "disconnected": True,
        "hint": "已停用 LINE 推播並清除本機 config 內的 token 與收件 id；環境變數需自行移除。",
    }
