"""Telegram 小秘書對話（ADR-013）：手機上就是那個交辦框。

沿用既有的 ``getUpdates`` 長輪詢通道（只有 outbound HTTPS，不開 inbound
port），把綁定 chat 的自由文字轉給 :func:`core.secretary_ask.ask_secretary`
——與儀表板對話框同一條管線（記憶區脈絡＋RAG 檢索＋LLM）。

安全與隱私契約：

- **獨立開關、預設關閉**：``notifiers.telegram.chat.enabled``。關閉時行為與
  今天完全一樣（只有通知、``/proposals`` 與 inline 批准）。
- **內容會經過 Telegram**：這是本專案唯一會把「你的提問與秘書的回答」送出
  本機的通道，因此預設關閉、在設定與文件中明講。引用只送檔名，不送文件內容
  切片；若 LLM provider 選的是雲端供應商，內容另會送往該供應商（與網頁相同）。
- **chat 綁定不變**：只處理設定 chat id 的訊息；其他 chat 靜默忽略。
- **批准仍是 ADR-008 的兩道門**：Telegram 只能批准 server 白名單的 L0/L1，
  且通道必須先 arm。``/arm`` 收的是儀表板簽發的**一次性 6 位數短效碼**
  （ADR-014；5 分鐘失效、用過即銷毀），手機因此永遠不需要持有長期的
  execution token；另有開關 ``executor.telegram_approvals.allow_remote_arm``
  （預設關閉）。訊息仍會被刪除當作多一層防護。``/disarm`` 是降低權限的方向，
  永遠可用、不需任何開關。
- **不阻塞 poller**：慢的問答丟到背景執行緒，同時間只允許一題；批准按鈕與
  其他指令不會被一題長回答卡住。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Optional

from core.config import get_config
from core.time_utils import get_local_now
from notifiers.telegram_setup import Transport, _call_api

logger = logging.getLogger("OmniContext.TelegramChat")

MESSAGE_CHUNK_CHARS = 3500
DEFAULT_MAX_QUESTION_CHARS = 1000
MAX_CITATIONS_LISTED = 4

CHAT_CLAIM_BOUNDARY = (
    "Telegram 對話與儀表板交辦框走同一條管線（記憶區脈絡＋知識庫檢索＋所選 LLM）；"
    "提問與回答會經過 Telegram 伺服器，引用只送檔名不送文件內容。"
)

_ASK_LOCK = threading.Lock()
_ASK_IN_FLIGHT = False
_ASKS_ANSWERED = 0


def telegram_chat_enabled(cfg: Any | None = None) -> bool:
    """雙開關疊加，皆預設關閉（通知本身要開，對話另外要開）。"""
    cfg = cfg or get_config()
    return bool(cfg.get("notifiers.telegram.enabled", False)) and bool(
        cfg.get("notifiers.telegram.chat.enabled", False)
    )


def remote_arm_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(
        cfg.get("proactive_secretary.executor.telegram_approvals.allow_remote_arm", False)
    )


def telegram_updates_poller_enabled(cfg: Any | None = None) -> bool:
    """對話或批准任一啟用就需要長輪詢；兩者都關就完全不開 poller。"""
    from notifiers.telegram_approvals import telegram_approvals_enabled

    cfg = cfg or get_config()
    return telegram_approvals_enabled(cfg) or telegram_chat_enabled(cfg)


def _max_question_chars(cfg: Any) -> int:
    try:
        value = int(cfg.get("notifiers.telegram.chat.max_question_chars", DEFAULT_MAX_QUESTION_CHARS))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_QUESTION_CHARS
    return max(50, min(value, 4000))


def chat_status(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    with _ASK_LOCK:
        in_flight = _ASK_IN_FLIGHT
        answered = _ASKS_ANSWERED
    return {
        "enabled": telegram_chat_enabled(cfg),
        "remote_arm_enabled": remote_arm_enabled(cfg),
        "ask_in_flight": in_flight,
        "asks_answered": answered,
        "claim_boundary": CHAT_CLAIM_BOUNDARY,
    }


def _reset_state_for_tests() -> None:
    global _ASK_IN_FLIGHT, _ASKS_ANSWERED
    with _ASK_LOCK:
        _ASK_IN_FLIGHT = False
        _ASKS_ANSWERED = 0


# ---- 傳送 ----


def send_text(
    token: str, chat: str, text: str, *, transport: Optional[Transport] = None
) -> None:
    """Telegram 單則上限 4096；長答案切段送出，不靜默截斷。"""
    body = text or ""
    chunks = [body[i : i + MESSAGE_CHUNK_CHARS] for i in range(0, len(body), MESSAGE_CHUNK_CHARS)] or [""]
    for chunk in chunks:
        try:
            _call_api(
                token,
                "sendMessage",
                {"chat_id": chat, "text": chunk, "disable_web_page_preview": True},
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001 — 送不出去只記型別，不中斷 poller
            logger.warning("sendMessage failed: %s", type(exc).__name__)
            return


def _delete_message(
    token: str, chat: str, message_id: Any, *, transport: Optional[Transport] = None
) -> bool:
    """刪掉帶解鎖碼的訊息（多一層防護；碼本身是一次性短效的）。"""
    if message_id is None:
        return False
    try:
        status_code, body = _call_api(
            token,
            "deleteMessage",
            {"chat_id": chat, "message_id": message_id},
            transport=transport,
        )
        return status_code == 200 and bool(body.get("ok", True))
    except Exception as exc:  # noqa: BLE001
        logger.warning("deleteMessage failed: %s", type(exc).__name__)
        return False


# ---- 指令 ----


HELP_TEXT = (
    "OmniContext 小秘書（手機）\n"
    "\n"
    "直接打字＝向小秘書提問（會帶今日狀態、提案與你的筆記）。\n"
    "\n"
    "／today　今天：上次做到哪、早晨包、前幾個建議\n"
    "／proposals　待判斷建議（附可批准按鈕）\n"
    "／notes　記憶區最近的筆記\n"
    "／status　開關、批准通道與記憶區狀態\n"
    "／arm <6 位數碼>　解鎖遠端批准（碼在儀表板產生，5 分鐘失效、用一次）\n"
    "／disarm　立刻上鎖批准通道（隨時可用）\n"
    "\n"
    "記下來：… ／ 偏好：… ／ 決定：… 會直接寫進記憶區，不送 LLM。\n"
    "偏好可寫「不要提醒 <專案或提案類型>」來壓掉建議。"
)


def _today_text(cfg: Any, now: datetime) -> str:
    from core.secretary_packs import build_today_view

    try:
        view = build_today_view(cfg=cfg, now=now)
    except Exception as exc:  # noqa: BLE001
        return f"（今日視圖讀不到：{type(exc).__name__}）"
    lines = [f"📅 今天 {now.strftime('%m-%d %H:%M')}"]
    resume = view.get("resume") or {}
    if resume.get("display_name") or resume.get("project_key"):
        lines.append(
            f"上次做到哪：{resume.get('display_name') or resume.get('project_key')}"
            f"（{str(resume.get('last_activity_at') or '')[:16]}）"
        )
        if resume.get("last_action_summary"):
            lines.append(f"　{str(resume['last_action_summary'])[:200]}")
    if view.get("pack_line"):
        lines.append(str(view["pack_line"]))
    lines.append(f"進行中專案：{view.get('active_project_count', 0)} 個")

    try:
        from core.proactive_secretary import build_action_proposals

        proposals = build_action_proposals(cfg=cfg, now=now, limit=3).get("proposals", [])
    except Exception as exc:  # noqa: BLE001
        proposals = []
        lines.append(f"（建議讀不到：{type(exc).__name__}）")
    if proposals:
        lines.append("")
        lines.append("待判斷建議：")
        for item in proposals:
            lines.append(f"• [{item.get('project_key')}] {item.get('title')}")
            if item.get("why_now"):
                lines.append(f"　為什麼是現在：{item['why_now']}")
    lines.append("")
    lines.append("要批准請用 /proposals（附按鈕）。")
    return "\n".join(lines)


def _notes_text(limit: int = 8) -> str:
    from core.secretary_memory import list_notes

    try:
        listed = list_notes(limit=max(1, min(limit, 30)))
    except Exception as exc:  # noqa: BLE001
        return f"（記憶區讀不到：{type(exc).__name__}）"
    notes = listed.get("notes") or []
    counts = listed.get("counts") or {}
    header = (
        f"🧠 記憶區共 {listed.get('total', 0)} 筆"
        f"（筆記 {counts.get('user_note', 0)}／偏好 {counts.get('preference', 0)}／"
        f"決定 {counts.get('decision', 0)}／觀察 {counts.get('observation', 0)}）"
    )
    if not notes:
        return header + "\n還沒有任何記憶。打「記下來：…」就會寫進來。"
    lines = [header, ""]
    for note in notes:
        stamp = str(note.get("created_at") or "")[5:16].replace("T", " ")
        project = f"[{note['project_key']}] " if note.get("project_key") else ""
        lines.append(f"• ({note.get('kind_label')} {stamp}) {project}{str(note.get('body') or '')[:200]}")
    lines.append("")
    lines.append("刪除請到儀表板 01 的記憶區面板（每筆都有 ✕）。")
    return "\n".join(lines)


def _status_text(cfg: Any, now: datetime) -> str:
    from notifiers.telegram_approvals import approvals_status

    lines = ["⚙️ 狀態"]
    approvals = approvals_status(cfg=cfg, now=now)
    lines.append(f"對話：{'開' if telegram_chat_enabled(cfg) else '關'}")
    lines.append(f"inline 批准：{'開' if approvals.get('enabled') else '關'}")
    if approvals.get("armed"):
        lines.append(f"批准通道：已解鎖，到 {str(approvals.get('armed_until'))[:16]}")
    else:
        lines.append("批准通道：上鎖（按鈕會拒絕執行）")
    lines.append(f"遠端 /arm：{'允許' if remote_arm_enabled(cfg) else '未開放（只能在儀表板解鎖）'}")
    lines.append(f"長輪詢：{'運行中' if approvals.get('poller_running') else '未運行'}")
    try:
        from core.secretary_memory import list_notes

        lines.append(f"記憶區：{list_notes(limit=1).get('total', 0)} 筆")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"記憶區：讀不到（{type(exc).__name__}）")
    with _ASK_LOCK:
        lines.append(f"本次啟動已回答 {_ASKS_ANSWERED} 題" + ("（目前有一題進行中）" if _ASK_IN_FLIGHT else ""))
    return "\n".join(lines)


def _handle_arm(
    raw: str,
    *,
    cfg: Any,
    token: str,
    chat: str,
    message_id: Any,
    transport: Optional[Transport],
    now: datetime,
) -> dict[str, Any]:
    """`/arm <6 位數 code>`：一次性短效碼（ADR-014），不再是長期 execution token。

    手機因此永遠不需要持有 execution token；碼在儀表板按一下取得，5 分鐘失效、
    用過即銷毀，留在聊天記錄裡也沒有長期價值。收到仍會嘗試刪除訊息（多一層防護）。
    """
    from notifiers.telegram_approvals import arm_approvals, consume_arm_code

    _delete_message(token, chat, message_id, transport=transport)

    if not remote_arm_enabled(cfg):
        send_text(
            token,
            chat,
            "遠端解鎖未開放。請在儀表板「設定 → Telegram 通知 → 🔓 解鎖遠端批准」直接解鎖，"
            "或先開啟 executor.telegram_approvals.allow_remote_arm。",
            transport=transport,
        )
        return {"handled": "remote_arm_disabled"}

    parts = raw.split(maxsplit=1)
    provided = parts[1].strip() if len(parts) > 1 else ""
    if not provided:
        send_text(
            token,
            chat,
            "用法：/arm <6 位數碼>。在儀表板「設定 → Telegram 通知 → 🔑 產生解鎖碼」取得，"
            "5 分鐘內有效、只能用一次。",
            transport=transport,
        )
        return {"handled": "arm_missing_code"}

    accepted, reason = consume_arm_code(provided, cfg=cfg, now=now)
    if not accepted:
        hint = {
            "no_pending_code": "目前沒有待驗的解鎖碼；請在儀表板按「🔑 產生解鎖碼」。",
            "code_expired": "這個碼已過期（5 分鐘）；請重新產生一組。",
            "code_mismatch": "碼不正確；為安全起見剛才那組已作廢，請重新產生。",
        }.get(reason, "解鎖碼無效。")
        send_text(token, chat, f"批准通道維持上鎖。{hint}", transport=transport)
        return {"handled": "arm_rejected", "reason": reason}

    try:
        receipt = arm_approvals(cfg=cfg, now=now)
    except Exception as exc:  # noqa: BLE001 — 例如 telegram_approvals 未啟用
        send_text(token, chat, f"沒有解鎖：{getattr(exc, 'error_code', type(exc).__name__)}", transport=transport)
        return {"handled": "arm_failed"}
    send_text(
        token,
        chat,
        f"🔓 批准通道已解鎖至 {str(receipt.get('armed_until'))[:16]}"
        f"（{receipt.get('ttl_hours')} 小時；服務重啟即失效）。"
        "仍只能批准白名單的 L0/L1 動作，L2 要回儀表板輸入確認碼。",
        transport=transport,
    )
    return {"handled": "armed", "armed_until": receipt.get("armed_until")}


# ---- 問答 ----


def _default_submit(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="TelegramAsk", daemon=True).start()


def _format_answer(result: dict[str, Any]) -> str:
    lines = [str(result.get("answer") or "").strip()]
    citations = result.get("citations") or []
    if citations:
        names = [str(c.get("filename") or "?") for c in citations[:MAX_CITATIONS_LISTED]]
        more = f" 等 {len(citations)} 則" if len(citations) > MAX_CITATIONS_LISTED else ""
        lines.append("")
        lines.append(f"📎 引用：{'、'.join(names)}{more}")
    memory = result.get("memory") or {}
    if memory.get("included"):
        lines.append(f"🧠 參考記憶區 {memory.get('notes_used', 0)} 筆")
    return "\n".join(part for part in lines if part is not None)


def _run_ask(
    question: str,
    *,
    cfg: Any,
    token: str,
    chat: str,
    transport: Optional[Transport],
    ask: Callable[..., dict[str, Any]],
) -> None:
    global _ASK_IN_FLIGHT, _ASKS_ANSWERED
    try:
        result = ask(
            question,
            provider=(cfg.get("notifiers.telegram.chat.provider", "") or None),
            model=(cfg.get("notifiers.telegram.chat.model", "") or None),
            enable_rag=bool(cfg.get("notifiers.telegram.chat.enable_rag", True)),
            cfg=cfg,
        )
        send_text(token, chat, _format_answer(result), transport=transport)
    except Exception as exc:  # noqa: BLE001 — 任何失敗都要回一句話，不能安靜消失
        logger.error("Telegram ask failed: %s", exc, exc_info=True)
        send_text(token, chat, f"（回答失敗：{type(exc).__name__}）", transport=transport)
    finally:
        with _ASK_LOCK:
            _ASK_IN_FLIGHT = False
            _ASKS_ANSWERED += 1


def _handle_question(
    question: str,
    *,
    cfg: Any,
    token: str,
    chat: str,
    transport: Optional[Transport],
    ask: Optional[Callable[..., dict[str, Any]]],
    submit: Optional[Callable[[Callable[[], None]], None]],
) -> dict[str, Any]:
    global _ASK_IN_FLIGHT
    limit = _max_question_chars(cfg)
    if len(question) > limit:
        send_text(token, chat, f"問題太長了（上限 {limit} 字）。", transport=transport)
        return {"handled": "question_too_long"}
    with _ASK_LOCK:
        if _ASK_IN_FLIGHT:
            send_text(token, chat, "上一題還在回答中，等這題回完再問。", transport=transport)
            return {"handled": "chat_busy"}
        _ASK_IN_FLIGHT = True

    if ask is None:
        from core.secretary_ask import ask_secretary as ask

    send_text(token, chat, "🤔 查一下…", transport=transport)
    runner = submit or _default_submit
    runner(lambda: _run_ask(question, cfg=cfg, token=token, chat=chat, transport=transport, ask=ask))
    return {"handled": "chat_answering"}


# ---- 入口 ----


def handle_chat_message(
    text: str,
    *,
    cfg: Any,
    token: str,
    chat: str,
    message_id: Any = None,
    transport: Optional[Transport] = None,
    now: datetime | None = None,
    ask: Optional[Callable[..., dict[str, Any]]] = None,
    submit: Optional[Callable[[Callable[[], None]], None]] = None,
) -> dict[str, Any]:
    """處理綁定 chat 的一則文字訊息；呼叫端已完成 chat 綁定檢查。"""
    from core.secretary_memory import add_note, parse_note_command

    now = now or get_local_now()
    raw = (text or "").strip()
    if not raw:
        return {"handled": "message_ignored"}
    lowered = raw.lower()

    if lowered.startswith("/help") or lowered.startswith("/start"):
        send_text(token, chat, HELP_TEXT, transport=transport)
        return {"handled": "help_sent"}
    if lowered.startswith("/today"):
        send_text(token, chat, _today_text(cfg, now), transport=transport)
        return {"handled": "today_sent"}
    if lowered.startswith("/notes"):
        parts = raw.split()
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 8
        send_text(token, chat, _notes_text(limit), transport=transport)
        return {"handled": "notes_sent"}
    if lowered.startswith("/status"):
        send_text(token, chat, _status_text(cfg, now), transport=transport)
        return {"handled": "status_sent"}
    if lowered.startswith("/disarm"):
        from notifiers.telegram_approvals import disarm_approvals

        disarm_approvals()
        send_text(token, chat, "🔒 批准通道已上鎖；按鈕不會再執行任何動作。", transport=transport)
        return {"handled": "disarmed"}
    if lowered.startswith("/arm"):
        return _handle_arm(
            raw, cfg=cfg, token=token, chat=chat, message_id=message_id, transport=transport, now=now
        )

    # 記下來／偏好／決定：直接寫記憶區，不送 LLM（與網頁交辦框同一套規則）
    note_command = parse_note_command(raw)
    if note_command:
        try:
            note = add_note(source="telegram", now=now, **note_command)
        except Exception as exc:  # noqa: BLE001
            send_text(token, chat, f"沒有記下：{getattr(exc, 'error_code', type(exc).__name__)}", transport=transport)
            return {"handled": "note_rejected"}
        project = f" · {note['project_key']}" if note.get("project_key") else ""
        send_text(
            token,
            chat,
            f"🧠 已記下（{note.get('kind_label')}{project}）：{note.get('body')}\n"
            "之後的回答與建議都會參考；要刪除到儀表板 01 的記憶區面板。",
            transport=transport,
        )
        return {"handled": "note_saved", "note_id": note.get("id"), "kind": note.get("kind")}

    question = raw[4:].strip() if lowered.startswith("/ask") else raw
    if not question:
        send_text(token, chat, "用法：/ask <問題>，或直接打字提問。", transport=transport)
        return {"handled": "empty_question"}
    if question.startswith("/"):
        send_text(token, chat, "不認得這個指令。/help 看可用指令。", transport=transport)
        return {"handled": "unknown_command"}
    return _handle_question(
        question, cfg=cfg, token=token, chat=chat, transport=transport, ask=ask, submit=submit
    )
