"""秘書桌面（ADR-019）：01 分頁成為真正的首頁——卡片由秘書決定該顯示什麼。

儀表板有六個分頁、三十幾個面板；01 已經往對的方向走（三欄、今日行動、問候卡、記憶區），
但使用者仍要「去某個面板找某件事」。一個秘書的首頁應該是：大部分時候待在 01 就夠，
其他分頁是詳情。

這個模組**只重新排列既有的唯讀資料**，用確定性的規則挑出三樣東西：

- **焦點**：提案引擎排序後的第一張（分數已含 ADR-017 習慣加權與 ADR-018 你宣告的優先），
  附「為什麼是現在」與既有的可執行動作（executor 開著才有；執行仍需批准）。
- **記得**：一則筆記，依固定順序挑——與焦點專案有關的決定／筆記 → 最近一天的工作誌
  （日層、未過期）→ 你釘選的 → 你最近記下的；挑不到就如實說沒有。
- **上次做到哪**、行事曆一句、個人檔案一行、各詳情面板的計數。

不呼叫 LLM、不寫任何資料、沒有新表；每一節各自隔離失敗（``sections`` 如實回報），
一節壞了其他節照出。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.config import get_config
from core.database import get_db
from core.models import SecretaryNote
from core.secretary_memory import observation_ttl, serialize_note
from core.time_utils import get_local_now

HOME_CLAIM_BOUNDARY = (
    "首頁只重新排列既有的唯讀資料：焦點＝提案引擎排序後的第一張（含你宣告的優先與習慣加權）、"
    "記得＝依固定順序挑的一則筆記（焦點專案的決定 → 最近一天的工作誌 → 釘選 → 最近記下的）。"
    "規則是確定性的，不呼叫 LLM、不寫任何資料；完整清單仍在下方詳情與其他分頁。"
)

MEMORY_PICK_RULES: dict[str, str] = {
    "focus_project": "與焦點專案有關的決定／筆記",
    "daily_digest": "最近一天的工作誌",
    "pinned": "你釘選的",
    "recent": "你最近記下的",
}
NO_MEMORY_HINT = "還沒有可挑的記憶；在對話框打「記下來：…」，或讓每日工作誌跑一天。"
_MEMORY_SCAN_LIMIT = 300

# 這兩種提案講的是 OmniContext 自己的設定（extension 沒 heartbeat、秘書沒有每日排程），不是你的工作。
# 它們留在完整清單裡，但只有在沒有任何工作提案時才佔焦點——首頁的焦點該是你的事，不是工具的事。
SYSTEM_PROPOSAL_TYPES: frozenset[str] = frozenset({"verify_extension_heartbeat", "no_daily_routine"})


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def _created(note: dict[str, Any]) -> datetime | None:
    raw = note.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def pick_memory(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    focus_project_key: str | None = None,
) -> dict[str, Any]:
    """依固定順序挑一則筆記；回傳 ``{"note", "rule", "why_this"}``，挑不到時 note 為 None 並附 hint。

    順序是刻意的：先給「你接下來要動的那件事」相關的決定（最可能改變你現在的動作），
    再給「你昨天做了什麼」，再給你自己釘起來的，最後才是最新的一則。
    """
    database = database or get_db()
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryNote)
            .order_by(SecretaryNote.created_at.desc(), SecretaryNote.id.desc())
            .limit(_MEMORY_SCAN_LIMIT)
            .all()
        )
        notes = [serialize_note(row) for row in rows]

    def _result(note: dict[str, Any], rule: str) -> dict[str, Any]:
        return {"note": note, "rule": rule, "why_this": MEMORY_PICK_RULES[rule]}

    if focus_project_key:
        wanted = str(focus_project_key).casefold()
        for note in notes:
            if note["kind"] in ("decision", "user_note") and str(note.get("project_key") or "").casefold() == wanted:
                return _result(note, "focus_project")
    cutoff = now - observation_ttl(cfg)
    for note in notes:
        created = _created(note)
        if (
            note["kind"] == "observation"
            and note.get("source") == "daily_digest"
            and not note.get("project_key")
            and created is not None
            and created >= cutoff
        ):
            return _result(note, "daily_digest")
    for note in notes:
        if note.get("pinned") and note["kind"] != "observation":
            return _result(note, "pinned")
    for note in notes:
        if note["kind"] in ("decision", "user_note"):
            return _result(note, "recent")
    return {"note": None, "rule": None, "why_this": None, "hint": NO_MEMORY_HINT}


def _choose_focus(proposals: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    """第一張關於你的工作的提案；全部都是系統提醒時才退回第一張。回傳 (焦點, 被跳過的系統提醒類型)。"""
    skipped: list[str] = []
    for item in proposals:
        if str(item.get("proposal_type") or "") in SYSTEM_PROPOSAL_TYPES:
            skipped.append(str(item.get("proposal_type")))
            continue
        return item, skipped
    return (proposals[0] if proposals else None), ([] if proposals else skipped)


def build_home(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    proposals: list[dict[str, Any]] | None = None,
    today: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """組出 01 首頁「秘書桌面」的內容；每一節各自隔離失敗，收據在 ``sections``。"""
    database = database or get_db()
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    sections: dict[str, str] = {}

    # 1. 焦點：提案引擎排序後的第一張（引擎已含 mute／snooze／習慣加權／宣告優先）
    proposal_list: list[dict[str, Any]] = []
    try:
        if proposals is None:
            from core.proactive_secretary import build_action_proposals

            result = build_action_proposals(database=database, cfg=cfg, now=now)
            try:
                from core.agent_executor import attach_execution_actions

                result = attach_execution_actions(result, cfg=cfg, database=database, now=now)
            except Exception as exc:  # noqa: BLE001 — 動作標記失敗只少掉批准按鈕
                sections["actions"] = f"error:{type(exc).__name__}"
            proposal_list = list(result.get("proposals") or [])
        else:
            proposal_list = list(proposals)
        sections["focus"] = "ok"
    except Exception as exc:  # noqa: BLE001
        sections["focus"] = f"error:{type(exc).__name__}"
    focus, skipped_system = _choose_focus(proposal_list)

    # 2. 今日視圖：上次做到哪、行事曆、早晨包一行、記憶區計數
    try:
        if today is None:
            from core.secretary_packs import build_today_view

            today = build_today_view(database=database, cfg=cfg, now=now)
        sections["today"] = "ok"
    except Exception as exc:  # noqa: BLE001
        today = {}
        sections["today"] = f"error:{type(exc).__name__}"
    today = today or {}

    # 3. 記得：一則筆記
    try:
        memory_pick = pick_memory(
            database=database, cfg=cfg, now=now,
            focus_project_key=(focus or {}).get("project_key"),
        )
        sections["memory"] = "ok"
    except Exception as exc:  # noqa: BLE001
        memory_pick = {"note": None, "rule": None, "why_this": None, "hint": NO_MEMORY_HINT}
        sections["memory"] = f"error:{type(exc).__name__}"

    # 4. 個人檔案一行（ADR-018）
    profile_line = ""
    try:
        from core.secretary_profile import load_profile, profile_summary_line

        profile_line = profile_summary_line(load_profile(database=database))
        sections["profile"] = "ok"
    except Exception as exc:  # noqa: BLE001
        sections["profile"] = f"error:{type(exc).__name__}"

    resume = today.get("resume") or {}
    memory_meta = today.get("memory") or {}
    calendar = today.get("calendar") or {}
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "focus": {
            "proposal": focus,
            "total": len(proposal_list),
            "remaining": max(0, len(proposal_list) - 1),
            "skipped_system": skipped_system,
            "basis": (
                "提案引擎排序後第一張「關於你的工作」的提案（分數含 mute／snooze／習慣加權／宣告優先）；"
                "OmniContext 自身的設定提醒只在沒有別的可看時才佔焦點"
            ),
        },
        "memory_pick": memory_pick,
        "resume": resume,
        "calendar": calendar,
        "pack_line": today.get("pack_line"),
        "profile_line": profile_line,
        "details": {
            "proposals": len(proposal_list),
            "notes": int(memory_meta.get("total") or 0),
            "notes_counts": memory_meta.get("counts") or {},
            "active_projects": int(today.get("active_project_count") or 0),
            "open_loops": resume.get("open_loops_count"),
            "calendar_events": int(calendar.get("count") or 0),
        },
        "sections": sections,
        "claim_boundary": HOME_CLAIM_BOUNDARY,
    }
