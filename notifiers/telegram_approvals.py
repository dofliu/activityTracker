"""P5-R4b：Telegram inline 批准與建議推播（ADR-008 階段 4）。

架構：本機服務以 ``getUpdates`` **長輪詢**接收按鈕回呼——只有 outbound
HTTPS，不開任何 inbound port，loopback-only 邊界不變。

安全契約（疊加在 ADR-008 D1–D6 之上）：

- **同一 execution token 邊界**：批准通道必須先由儀表板上一個帶
  ``x-omnicontext-execution-token`` 的請求「解鎖」（arm）。armed 狀態
  只存在記憶體（服務重啟即失效）且有 TTL（預設 24h）；未 arm 時所有
  按鈕一律回「通道未啟用」，不執行任何動作。
- **雙開關**：``executor.enabled`` ＋ ``executor.telegram_approvals.enabled``
  （皆預設關閉）。關閉時不啟動 poller、不附任何批准按鈕。
- **chat 綁定**：只處理來自設定 chat id 的 update；其他 chat 的訊息與
  回呼一律靜默忽略（只記次數，不記內容）。
- **只批 L1**：L2 需要一次性確認碼，Telegram 不支援——若按到 L2
  按鈕，立即作廢剛簽發的 confirm code 並提示回儀表板。L0 唯讀動作
  照常可執行。
- **D1 不變**：callback 只攜帶 ``ap:<proposal_id>:<template_id>``；
  proposal 由 server 端即時重建，evidence 改變即 404，呼叫端（Telegram
  按鈕）無法注入任何 command / path / argv。
- 每次批准寫入與 Web 相同的 audit receipt（``approved_via=telegram_inline``）。

replay 邊界（如實記載）：poller 採「處理後於下一輪 getUpdates 確認」的
標準模式；若處理後、確認前服務崩潰，該 update 會重放一次。可經此通道
執行的動作僅限 L0/L1 白名單（冪等或可逆），且 proposal 過期即 404、
active 唯一索引防重疊，重放的實際影響已被收斂。
"""

from __future__ import annotations

import logging
import threading
import time as time_module
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from core.agent_executor import (
    ExecutionRejected,
    attach_execution_actions,
    discard_pending_confirm,
    executor_enabled,
)
from core.config import get_config
from core.time_utils import get_local_now
from notifiers.telegram_setup import (
    Transport,
    _call_api,
    _resolve_bot_token,
    _resolve_chat_id,
)

logger = logging.getLogger("OmniContext.TelegramApprovals")

CALLBACK_PREFIX = "ap"
CALLBACK_DATA_LIMIT = 64  # Telegram callback_data 硬上限（bytes）
BUTTON_TEXT_LIMIT = 48
ANSWER_TEXT_LIMIT = 190
DEFAULT_ARM_TTL_HOURS = 24
DEFAULT_MAX_ACTIONS_PER_PUSH = 4
LONG_POLL_SECONDS = 25

APPROVALS_CLAIM_BOUNDARY = (
    "Telegram 只能批准 server 白名單的 L0/L1 動作；批准通道需先在儀表板以 "
    "execution token 解鎖（記憶體內、有 TTL、重啟即失效），L2 一律回儀表板走"
    "一次性確認碼流程。每次批准寫入 audit receipt（approved_via=telegram_inline）。"
)

# ---- in-memory 狀態（重啟即歸零；這是刻意的安全性質，不是缺陷） ----
_STATE_LOCK = threading.Lock()
_ARMED_UNTIL: datetime | None = None
_PROCESSED_CALLBACK_IDS: "OrderedDict[str, bool]" = OrderedDict()
_PROCESSED_CALLBACK_CAP = 300
_POLLER_RUNNING = False
_LAST_POLL_AT: datetime | None = None
_IGNORED_FOREIGN_UPDATES = 0


def telegram_approvals_enabled(cfg: Any | None = None) -> bool:
    """雙開關疊加，皆預設關閉。"""
    cfg = cfg or get_config()
    return executor_enabled(cfg) and bool(
        cfg.get("proactive_secretary.executor.telegram_approvals.enabled", False)
    )


def _arm_ttl_hours(cfg: Any) -> int:
    try:
        raw = int(
            cfg.get(
                "proactive_secretary.executor.telegram_approvals.arm_ttl_hours",
                DEFAULT_ARM_TTL_HOURS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_ARM_TTL_HOURS
    return min(24 * 7, max(1, raw))


def _max_actions_per_push(cfg: Any) -> int:
    try:
        raw = int(
            cfg.get(
                "proactive_secretary.executor.telegram_approvals.max_actions_per_push",
                DEFAULT_MAX_ACTIONS_PER_PUSH,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_ACTIONS_PER_PUSH
    return min(8, max(1, raw))


def arm_approvals(cfg: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    """解鎖批准通道；呼叫端（API 層）必須已通過 execution token 驗證。"""
    global _ARMED_UNTIL
    cfg = cfg or get_config()
    now = now or get_local_now()
    if not telegram_approvals_enabled(cfg):
        raise ExecutionRejected(
            "telegram_approvals_disabled",
            "Telegram 批准未啟用（executor.telegram_approvals.enabled=false）",
        )
    ttl_hours = _arm_ttl_hours(cfg)
    with _STATE_LOCK:
        _ARMED_UNTIL = now + timedelta(hours=ttl_hours)
        armed_until = _ARMED_UNTIL
    logger.info("Telegram approvals armed for %d hours.", ttl_hours)
    return {
        "armed": True,
        "armed_until": armed_until.isoformat(timespec="seconds"),
        "ttl_hours": ttl_hours,
        "claim_boundary": APPROVALS_CLAIM_BOUNDARY,
    }


def disarm_approvals() -> dict[str, Any]:
    """上鎖批准通道（降低權限的方向，不需 token）。"""
    global _ARMED_UNTIL
    with _STATE_LOCK:
        _ARMED_UNTIL = None
    return {"armed": False, "claim_boundary": APPROVALS_CLAIM_BOUNDARY}


def _is_armed(now: datetime | None = None) -> bool:
    now = now or get_local_now()
    with _STATE_LOCK:
        return _ARMED_UNTIL is not None and now < _ARMED_UNTIL


def approvals_status(cfg: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    now = now or get_local_now()
    with _STATE_LOCK:
        armed_until = _ARMED_UNTIL
        last_poll = _LAST_POLL_AT
        poller_running = _POLLER_RUNNING
        ignored = _IGNORED_FOREIGN_UPDATES
    armed = armed_until is not None and now < armed_until
    return {
        "enabled": telegram_approvals_enabled(cfg),
        "armed": armed,
        "armed_until": armed_until.isoformat(timespec="seconds") if armed else None,
        "poller_running": poller_running,
        "last_poll_at": last_poll.isoformat(timespec="seconds") if last_poll else None,
        "ignored_foreign_updates": ignored,
        "claim_boundary": APPROVALS_CLAIM_BOUNDARY,
    }


def _reset_state_for_tests() -> None:
    global _ARMED_UNTIL, _IGNORED_FOREIGN_UPDATES
    with _STATE_LOCK:
        _ARMED_UNTIL = None
        _PROCESSED_CALLBACK_IDS.clear()
        _IGNORED_FOREIGN_UPDATES = 0


# ---- 建議推播（訊息＋inline keyboard） ----


def build_proposals_push(
    *,
    cfg: Any | None = None,
    database: Any | None = None,
    now: datetime | None = None,
    limit: int = 6,
    proposals_result: dict[str, Any] | None = None,
) -> tuple[str, list[list[dict[str, str]]] | None, dict[str, Any]]:
    """組出建議清單文字與（可批准時的）inline keyboard。

    - 只有 L0/L1 且不需確認碼的動作會出現按鈕；L2 一律不附按鈕。
    - callback_data 固定 ``ap:<proposal_id>:<template_id>``，超過 64 bytes
      的組合直接略過（fail-closed，不截斷）。
    - 通道未啟用或未 arm 時只推唯讀清單，並如實註明。
    """
    cfg = cfg or get_config()
    now = now or get_local_now()
    if proposals_result is None:
        from core.proactive_secretary import build_action_proposals

        proposals_result = attach_execution_actions(
            build_action_proposals(database=database, cfg=cfg, now=now, limit=limit),
            cfg=cfg,
            database=database,
            now=now,
        )
    proposals = proposals_result.get("proposals", [])
    stats = {"total": len(proposals), "actionable_buttons": 0}
    if not proposals:
        return "🤖 目前沒有待判斷的秘書建議。", None, stats

    armed = telegram_approvals_enabled(cfg) and _is_armed(now)
    lines = ["🤖 秘書建議（依優先序）："]
    keyboard: list[list[dict[str, str]]] = []
    max_buttons = _max_actions_per_push(cfg)
    for index, item in enumerate(proposals, start=1):
        priority = str(item.get("priority") or "low")
        marker = {"high": "🔴", "medium": "🟡"}.get(priority, "⚪")
        title = str(item.get("title") or "")[:120]
        project = str(item.get("project_key") or "")
        lines.append(f"{index}. {marker} [{project}] {title}")
        action = str(item.get("suggested_action") or "")
        if action:
            lines.append(f"   ↳ {action[:150]}")
        if not armed or stats["actionable_buttons"] >= max_buttons:
            continue
        for candidate in item.get("actions") or []:
            if candidate.get("requires_confirmation"):
                continue  # L2 一律回儀表板
            callback_data = (
                f"{CALLBACK_PREFIX}:{item.get('proposal_id')}:{candidate.get('template_id')}"
            )
            if len(callback_data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
                continue
            label = str(candidate.get("label") or candidate.get("template_id") or "")
            keyboard.append(
                [
                    {
                        "text": f"✅ {index} · {label}"[:BUTTON_TEXT_LIMIT],
                        "callback_data": callback_data,
                    }
                ]
            )
            stats["actionable_buttons"] += 1
            break  # 每個 proposal 只放 primary 一顆按鈕，保持清單可讀

    if armed:
        lines.append("")
        lines.append("點按下方按鈕即批准執行（L0/L1 白名單動作，寫 audit receipt）。")
    elif telegram_approvals_enabled(cfg):
        lines.append("")
        lines.append("（批准通道未解鎖：到儀表板 Telegram 卡片按「啟用遠端批准」後才會出現按鈕）")
    return "\n".join(lines), (keyboard or None), stats


def push_proposals_to_telegram(
    header: str = "",
    *,
    cfg: Any | None = None,
    database: Any | None = None,
    now: datetime | None = None,
    transport: Optional[Transport] = None,
    proposals_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把建議清單（含可批准按鈕）推到設定的 chat；無憑證時如實跳過。"""
    cfg = cfg or get_config()
    token, _ = _resolve_bot_token(cfg)
    chat, _ = _resolve_chat_id(cfg)
    if not token or not chat:
        return {"sent": False, "reason": "telegram_not_configured"}
    text, keyboard, stats = build_proposals_push(
        cfg=cfg, database=database, now=now, proposals_result=proposals_result
    )
    if header:
        text = f"{header}\n{text}"
    payload: dict[str, Any] = {
        "chat_id": chat,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        status_code, body = _call_api(token, "sendMessage", payload, transport=transport)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram proposals push failed: %s", type(exc).__name__)
        return {"sent": False, "reason": "network_unreachable", **stats}
    if status_code != 200 or not body.get("ok"):
        logger.warning("Telegram proposals push rejected: HTTP %s", status_code)
        return {"sent": False, "reason": f"telegram_api_{status_code}", **stats}
    return {"sent": True, **stats}


# ---- update 處理（poller 與 contract tests 共用同一入口） ----


def _remember_callback(callback_id: str) -> bool:
    """回傳 True 表示第一次見到；重複的 callback 不再處理。"""
    with _STATE_LOCK:
        if callback_id in _PROCESSED_CALLBACK_IDS:
            return False
        _PROCESSED_CALLBACK_IDS[callback_id] = True
        while len(_PROCESSED_CALLBACK_IDS) > _PROCESSED_CALLBACK_CAP:
            _PROCESSED_CALLBACK_IDS.popitem(last=False)
    return True


def _answer_callback(
    token: str,
    callback_id: str,
    text: str,
    *,
    show_alert: bool = False,
    transport: Optional[Transport] = None,
) -> None:
    try:
        _call_api(
            token,
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text[:ANSWER_TEXT_LIMIT],
                "show_alert": show_alert,
            },
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 — 回覆失敗只記型別，不中斷 poller
        logger.warning("answerCallbackQuery failed: %s", type(exc).__name__)


def _send_text(
    token: str, chat: str, text: str, *, transport: Optional[Transport] = None
) -> None:
    try:
        _call_api(
            token,
            "sendMessage",
            {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("sendMessage failed: %s", type(exc).__name__)


def parse_callback_data(data: str) -> tuple[str, str] | None:
    parts = str(data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def handle_telegram_update(
    update: dict[str, Any],
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    transport: Optional[Transport] = None,
    execute: Optional[Callable[..., dict[str, Any]]] = None,
    push_proposals: Optional[Callable[..., dict[str, Any]]] = None,
    chat_handler: Optional[Callable[..., dict[str, Any]]] = None,
) -> dict[str, Any]:
    """處理單一 update；回傳非敏感 receipt 供 log 與 contract tests。"""
    global _IGNORED_FOREIGN_UPDATES
    cfg = cfg or get_config()
    now = now or get_local_now()
    token, _ = _resolve_bot_token(cfg)
    chat_configured, _ = _resolve_chat_id(cfg)
    if not token or not chat_configured:
        return {"handled": "skipped_not_configured"}

    callback = update.get("callback_query")
    if callback:
        callback_id = str(callback.get("id") or "")
        from_chat = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        if from_chat != str(chat_configured):
            # 非綁定 chat：靜默忽略（不回覆、不外洩任何存在性資訊）。
            with _STATE_LOCK:
                _IGNORED_FOREIGN_UPDATES += 1
            return {"handled": "ignored_foreign_chat"}
        if not callback_id or not _remember_callback(callback_id):
            return {"handled": "duplicate_callback"}
        if not telegram_approvals_enabled(cfg) or not _is_armed(now):
            _answer_callback(
                token,
                callback_id,
                "🔒 批准通道未解鎖：請在儀表板 Telegram 卡片按「啟用遠端批准」（需 execution token）",
                show_alert=True,
                transport=transport,
            )
            return {"handled": "refused_not_armed"}
        parsed = parse_callback_data(str(callback.get("data") or ""))
        if parsed is None:
            _answer_callback(token, callback_id, "⚠️ 無效的操作代碼", transport=transport)
            return {"handled": "invalid_callback_data"}
        proposal_id, template_id = parsed

        execute = execute or _default_execute
        try:
            result = execute(
                proposal_id,
                approved_via="telegram_inline",
                template_id=template_id,
            )
        except ExecutionRejected as exc:
            _answer_callback(
                token,
                callback_id,
                f"⚠️ 未執行：{exc.error_code}",
                show_alert=True,
                transport=transport,
            )
            return {"handled": "execution_rejected", "error_code": exc.error_code}
        except Exception as exc:  # noqa: BLE001 — 不外洩內部細節
            logger.warning("Telegram approval execution failed: %s", type(exc).__name__)
            _answer_callback(token, callback_id, "⚠️ 執行失敗，詳見本機紀錄", transport=transport)
            return {"handled": "execution_error", "error_type": type(exc).__name__}

        if result.get("status") == "confirmation_required":
            # L2 不支援 Telegram：立即作廢剛簽發的 confirm code（fail-closed）。
            discard_pending_confirm(proposal_id)
            _answer_callback(
                token,
                callback_id,
                "🛡️ 此為 L2 動作，需要儀表板的一次性確認碼，無法由 Telegram 批准",
                show_alert=True,
                transport=transport,
            )
            return {"handled": "l2_refused_confirm_discarded"}

        receipt = result.get("receipt") or {}
        status = str(receipt.get("status") or "unknown")
        _answer_callback(token, callback_id, f"✅ 已執行：{status}", transport=transport)
        _send_text(
            token,
            chat_configured,
            f"✅ 批准執行完成：{receipt.get('template_id')} → {status}"
            f"（receipt #{receipt.get('id')}，approved_via=telegram_inline）",
            transport=transport,
        )
        return {
            "handled": "executed",
            "receipt_id": receipt.get("id"),
            "template_id": receipt.get("template_id"),
            "status": status,
        }

    message = update.get("message")
    if message:
        from_chat = str((message.get("chat") or {}).get("id"))
        if from_chat != str(chat_configured):
            with _STATE_LOCK:
                _IGNORED_FOREIGN_UPDATES += 1
            return {"handled": "ignored_foreign_chat"}
        text_raw = str(message.get("text") or "").strip()
        text = text_raw.lower()
        if text.startswith("/proposals"):
            push = push_proposals or push_proposals_to_telegram
            push(cfg=cfg, transport=transport)
            return {"handled": "proposals_pushed"}
        # ADR-013：對話開關開啟時，其餘訊息交給小秘書對話層（提問／筆記／指令）。
        from notifiers.telegram_chat import handle_chat_message, telegram_chat_enabled

        if telegram_chat_enabled(cfg):
            handler = chat_handler or handle_chat_message
            return handler(
                text_raw,
                cfg=cfg,
                token=token,
                chat=str(chat_configured),
                message_id=message.get("message_id"),
                transport=transport,
                now=now,
            )
        if text.startswith("/start") or text.startswith("/help"):
            _send_text(
                token,
                chat_configured,
                "OmniContext 秘書通知通道已連通。指令：/proposals 取得目前建議"
                "（批准按鈕需先在儀表板解鎖批准通道）。開啟"
                "「小秘書對話」後可直接在這裡提問與記筆記。",
                transport=transport,
            )
            return {"handled": "help_sent"}
        # 對話未啟用時，其他訊息一律不回應：這是通知/批准通道，不是聊天介面。
        return {"handled": "message_ignored"}

    return {"handled": "unsupported_update"}


def _default_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from core.agent_executor import execute_proposal

    return execute_proposal(*args, **kwargs)


# ---- getUpdates 長輪詢 poller ----


class TelegramApprovalPoller:
    """背景長輪詢執行緒；只有 outbound HTTPS，錯誤退避、可停止。"""

    def __init__(self, transport: Optional[Transport] = None):
        self._transport = transport
        self._running = False
        self._thread: threading.Thread | None = None
        self._offset: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="TelegramApprovalPoller", daemon=True
        )
        self._thread.start()
        logger.info("Telegram approval poller started (long polling; outbound only).")

    def stop(self) -> None:
        global _POLLER_RUNNING
        self._running = False
        with _STATE_LOCK:
            _POLLER_RUNNING = False
        # daemon thread；長輪詢最多再持續一個 timeout 週期後自然結束。

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._running)

    def _run(self) -> None:
        global _POLLER_RUNNING, _LAST_POLL_AT
        with _STATE_LOCK:
            _POLLER_RUNNING = True
        backoff = 5
        while self._running:
            try:
                cfg = get_config()
                token, _ = _resolve_bot_token(cfg)
                if not token:
                    time_module.sleep(30)
                    continue
                payload: dict[str, Any] = {
                    "timeout": LONG_POLL_SECONDS,
                    "allowed_updates": ["callback_query", "message"],
                }
                if self._offset is not None:
                    payload["offset"] = self._offset
                status_code, body = _call_api(
                    token,
                    "getUpdates",
                    payload,
                    transport=self._transport,
                    timeout=LONG_POLL_SECONDS + 10,
                )
                with _STATE_LOCK:
                    _LAST_POLL_AT = get_local_now()
                if status_code != 200 or not body.get("ok"):
                    logger.warning("getUpdates returned HTTP %s", status_code)
                    time_module.sleep(backoff)
                    backoff = min(60, backoff * 2)
                    continue
                backoff = 5
                for update in body.get("result") or []:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    if not self._running:
                        break
                    try:
                        receipt = handle_telegram_update(
                            update, cfg=cfg, transport=self._transport
                        )
                        if receipt.get("handled") not in (
                            "message_ignored",
                            "ignored_foreign_chat",
                        ):
                            logger.info("Telegram update handled: %s", receipt.get("handled"))
                    except Exception as exc:  # noqa: BLE001 — 單筆失敗不終止 poller
                        logger.error(
                            "Telegram update handling crashed: %s", type(exc).__name__
                        )
            except Exception as exc:  # noqa: BLE001 — 網路層錯誤退避重試
                logger.warning("Telegram poll cycle failed: %s", type(exc).__name__)
                time_module.sleep(backoff)
                backoff = min(60, backoff * 2)
        with _STATE_LOCK:
            _POLLER_RUNNING = False
        logger.info("Telegram approval poller stopped.")
