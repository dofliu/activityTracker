"""外部整合：Telegram、LINE、GitHub、LLM 金鑰狀態（TODO D4，ROADMAP §13 R1）。

連線設定與狀態查詢。secret 永不回流瀏覽器（redact/merge 機制不變）。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import logging

from core.agent_executor import ExecutionRejected
from core.api.deps import _require_execution_token
from core.config import get_config
from core.manager import get_manager
from core.schemas import LineConnectRequest, LineTestRequest, TelegramConnectRequest, TelegramDetectChatRequest, TelegramTestRequest
from core.secret_resolver import resolve_secret_env
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from typing import Optional

logger = logging.getLogger("OmniContext.Server")

router = APIRouter()


@router.get("/api/v1/telegram/status")
def get_telegram_status():
    """Telegram 設定現況（僅布林與來源標籤，不回傳 secret 值）。"""
    from notifiers.telegram_setup import telegram_status

    return telegram_status()


@router.post("/api/v1/telegram/test")
def test_telegram(payload: Optional[TelegramTestRequest] = None):
    """即時連線測試：getMe 驗 token；已有 chat id 時實發一則測試訊息。

    body 可帶尚未儲存的 bot_token / chat_id（先測後存）；一律不回傳
    secret 值，失敗以穩定 error_code + hint 呈現。
    """
    from notifiers.telegram_setup import test_telegram_connection

    payload = payload or TelegramTestRequest()
    return test_telegram_connection(
        bot_token=payload.bot_token,
        chat_id=payload.chat_id,
        send_test_message=payload.send_test_message,
    )


@router.post("/api/v1/telegram/detect-chat-id")
def detect_telegram_chat(payload: Optional[TelegramDetectChatRequest] = None):
    """getUpdates 列出最近對 bot 傳訊的對話候選，供使用者挑選 chat id。"""
    from notifiers.telegram_setup import detect_telegram_chat_id

    payload = payload or TelegramDetectChatRequest()
    return detect_telegram_chat_id(bot_token=payload.bot_token)


@router.post("/api/v1/telegram/connect")
def connect_telegram(payload: TelegramConnectRequest):
    """設定流程收尾：先即時驗證（getMe＋測試訊息），全部通過才寫入
    config.yaml 並熱套用排程；驗證失敗時 config 完全不動。"""
    from notifiers.telegram_setup import save_telegram_settings

    receipt = save_telegram_settings(
        bot_token=payload.bot_token,
        chat_id=payload.chat_id,
        enabled=payload.enabled,
        morning_briefing_time=payload.morning_briefing_time,
        evening_summary_time=payload.evening_summary_time,
    )
    if receipt.get("saved"):
        try:
            get_manager().reload_config()
            receipt["scheduler_reloaded"] = True
        except Exception:  # noqa: BLE001 — 設定已保存；重載失敗如實回報
            logger.warning("Scheduler reload after telegram connect failed", exc_info=True)
            receipt["scheduler_reloaded"] = False
            receipt["hint"] = "設定已保存，但排程重載失敗；重啟服務後生效"
    return receipt


@router.get("/api/v1/telegram/approvals/status")
def telegram_approvals_status():
    """批准通道現況（enabled/armed/poller），無 secret 值。"""
    from notifiers.telegram_approvals import approvals_status

    return approvals_status()


@router.post("/api/v1/telegram/approvals/arm")
def arm_telegram_approvals(request: Request):
    """解鎖 Telegram 批准通道（ADR-008 D4：同一 execution token 邊界）。

    armed 狀態只存記憶體且有 TTL；服務重啟即失效，需重新解鎖。
    """
    _require_execution_token(request)
    from notifiers.telegram_approvals import arm_approvals

    try:
        return arm_approvals()
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.post("/api/v1/telegram/approvals/arm-code")
def issue_telegram_arm_code(request: Request):
    """簽發一次性 arm code（ADR-014）：手機用它解鎖，不必持有 execution token。

    回傳值是唯一一次看到明碼的機會（只有雜湊留在記憶體、不寫 log）；碼短效、
    只能用一次，且只能用來解鎖批准通道。
    """
    _require_execution_token(request)
    from notifiers.telegram_approvals import issue_arm_code

    try:
        return issue_arm_code()
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.get("/api/v1/notifications/channels")
def get_notification_channels():
    """ADR-014 推播通道總覽（Telegram／LINE）與各自能力；不含任何 secret。"""
    from notifiers.channels import channels_status

    return channels_status()


@router.get("/api/v1/line/status")
def get_line_status():
    """LINE 設定現況（僅布林與來源標籤，不回傳 secret 值）。"""
    from notifiers.line_setup import line_status

    return line_status()


@router.post("/api/v1/line/test")
def test_line(payload: Optional[LineTestRequest] = None):
    """即時連線測試：/v2/bot/info 驗 token；已有收件 id 時實發一則測試訊息。"""
    from notifiers.line_setup import test_line_connection

    payload = payload or LineTestRequest()
    return test_line_connection(
        access_token=payload.access_token,
        to=payload.to,
        send_test_message=payload.send_test_message,
    )


@router.post("/api/v1/line/connect")
def connect_line(payload: LineConnectRequest):
    """設定流程收尾：先即時驗證，全部通過才寫 config 並熱套用排程。"""
    from notifiers.line_setup import save_line_settings

    receipt = save_line_settings(
        access_token=payload.access_token, to=payload.to, enabled=payload.enabled
    )
    if receipt.get("saved"):
        try:
            get_manager().reload_config()
            receipt["scheduler_reloaded"] = True
        except Exception:  # noqa: BLE001 — 設定已保存；重載失敗如實回報
            logger.warning("Scheduler reload after LINE connect failed", exc_info=True)
            receipt["scheduler_reloaded"] = False
            receipt["hint"] = "設定已保存，但排程重載失敗；重啟服務後生效"
    return receipt


@router.post("/api/v1/line/disconnect")
def disconnect_line_endpoint():
    """停用並清除 config 內的 LINE secret（環境變數不受影響）。"""
    from notifiers.line_setup import disconnect_line

    receipt = disconnect_line()
    try:
        get_manager().reload_config()
    except Exception:  # noqa: BLE001
        logger.warning("Scheduler reload after LINE disconnect failed", exc_info=True)
    return receipt


@router.get("/api/v1/telegram/chat/status")
def telegram_chat_status():
    """ADR-013 小秘書對話開關與狀態；唯讀，不含 token。"""
    from notifiers.telegram_chat import chat_status

    return chat_status()


@router.post("/api/v1/telegram/approvals/disarm")
def disarm_telegram_approvals():
    """上鎖批准通道（降低權限方向，不需 token）。"""
    from notifiers.telegram_approvals import disarm_approvals

    return disarm_approvals()


@router.post("/api/v1/telegram/disconnect")
def disconnect_telegram_endpoint():
    """停用並清除 config 內的 Telegram secret（環境變數不受影響）。"""
    from notifiers.telegram_setup import disconnect_telegram

    receipt = disconnect_telegram()
    try:
        get_manager().reload_config()
    except Exception:  # noqa: BLE001
        logger.warning("Scheduler reload after telegram disconnect failed", exc_info=True)
    return receipt


@router.get("/api/v1/llm/status")
def get_llm_secret_status():
    """Report provider credential availability without returning secret values."""
    cfg = get_config()
    providers = {}
    definitions = {
        "gemini": ("GEMINI_API_KEY", ("GOOGLE_API_KEY",)),
        "anthropic": ("ANTHROPIC_API_KEY", ()),
        "openai": ("OPENAI_API_KEY", ()),
    }
    for provider, (default_env, aliases) in definitions.items():
        env_name = str(
            cfg.get(f"synthesizer.{provider}.api_key_env", default_env)
            or default_env
        )
        providers[provider] = resolve_secret_env(env_name, aliases).public_status()

    providers["ollama"] = {
        "configured": True,
        "source": "local_service",
        "env_var": "",
    }
    selected = str(cfg.get("synthesizer.provider", "gemini") or "gemini").lower()
    return {
        "selected_provider": selected,
        "providers": providers,
        "secret_boundary": "status_only_no_secret_values",
    }
