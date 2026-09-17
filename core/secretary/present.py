"""秘書桌面（ADR-019）：01 分頁成為真正的首頁——卡片由秘書決定該顯示什麼。

儀表板有六個分頁、三十幾個面板；01 已經往對的方向走（三欄、今日行動、問候卡、記憶區），
但使用者仍要「去某個面板找某件事」。一個秘書的首頁應該是：大部分時候待在 01 就夠，
其他分頁是詳情。

這個模組**只重新排列既有的唯讀資料**，用確定性的規則挑出三樣東西：

- **焦點**：提案引擎排序後的第一張（分數已含 ADR-017 習慣加權與 ADR-018 你宣告的優先），
  附「為什麼是現在」與既有的可執行動作（executor 開著才有；執行仍需批准）。
- **記得**：一則筆記，依固定順序挑——與焦點專案有關的決定／筆記 → 剛出爐的每週回顧
  （ADR-020，三天內）→ 最近一天的工作誌（日層、未過期）→ 你釘選的 → 你最近記下的；
  挑不到就如實說沒有。
- **上次做到哪**、行事曆一句、個人檔案一行、各詳情面板的計數。

不呼叫 LLM、不寫任何資料、沒有新表；每一節各自隔離失敗（``sections`` 如實回報），
一節壞了其他節照出。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from core.config import get_config
from core.database import get_db
from core.models import SecretaryNote
from core.secretary.memory import chat_context_enabled, memory_context, observation_ttl, serialize_note
from core.secretary.packs import latest_pack_summary, pack_summary_line
from core.scheduled_tasks import presets_status
from core.time_utils import get_local_now
from core.agent_executor import executor_enabled, l2_enabled
from core.scheduled_tasks import legacy_opt_out, scheduled_tasks_enabled


HOME_CLAIM_BOUNDARY = (
    "首頁只重新排列既有的唯讀資料：焦點＝提案引擎排序後的第一張（含你宣告的優先與習慣加權）、"
    "記得＝依固定順序挑的一則筆記（焦點專案的決定 → 剛出爐的每週回顧 → 最近一天的工作誌 → 釘選 → 最近記下的）。"
    "規則是確定性的，不呼叫 LLM、不寫任何資料；完整清單仍在下方詳情與其他分頁。"
)

MEMORY_PICK_RULES: dict[str, str] = {
    "focus_project": "與焦點專案有關的決定／筆記",
    "weekly_review": "剛出爐的每週回顧（說的 vs 做的）",
    "daily_digest": "最近一天的工作誌",
    "pinned": "你釘選的",
    "recent": "你最近記下的",
}
NO_MEMORY_HINT = "還沒有可挑的記憶；在對話框打「記下來：…」，或讓每日工作誌跑一天。"
_MEMORY_SCAN_LIMIT = 300
WEEKLY_REVIEW_FRESH_DAYS = 3   # 每週回顧只在剛出爐的幾天內優先於每日工作誌

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
    # ADR-020：剛出爐的每週回顧（說的 vs 做的）比昨天的工作誌更值得先看，但只在頭幾天。
    fresh_cutoff = now - timedelta(days=WEEKLY_REVIEW_FRESH_DAYS)
    for note in notes:
        created = _created(note)
        if (
            note["kind"] == "observation"
            and note.get("source") == "weekly_review"
            and created is not None
            and created >= fresh_cutoff
        ):
            return _result(note, "weekly_review")
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
            from core.secretary.aggregate import build_action_proposals

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
        from core.secretary.memory import load_profile, profile_summary_line

        profile_line = profile_summary_line(load_profile(database=database))
        sections["profile"] = "ok"
    except Exception as exc:  # noqa: BLE001
        sections["profile"] = f"error:{type(exc).__name__}"

    resume = today.get("resume") or {}
    memory_meta = today.get("memory") or {}
    calendar = today.get("calendar") or {}
    # ADR-022：桌面在會議中多一行；訊號只有其一時如實說差異（來自 build_today_view）
    meeting = today.get("meeting") or {}
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
        "meeting": meeting,
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


# ---------------------------------------------------------------- 交辦框的回答（原 core/secretary_ask.py）
#
# 秘書單次問答（ADR-013）：把儀表板對話框那條管線包成一個同步呼叫。
# Web 的 `/api/v1/rag/chat` 是 SSE 串流，只適合瀏覽器。手機（Telegram）需要
# 「問一句、拿一整段答案」，因此這裡把同一條管線——**記憶區脈絡（ADR-012）
# ＋ RAG 檢索 ＋ LLM**——收斂成 :func:`ask_secretary`。
# 契約：
# - **同一條管線**：檢索沿用 ``rag.router._retrieve_citations``（worker 或
#   in_process 由設定決定），記憶區沿用 ``core.secretary_memory.memory_context``；
#   不另立一套規則，答案來源與網頁一致。
# - **絕不在模組層 import rag**：主服務 import ``core.server`` 不得載入
#   chromadb／fastembed／rank_bm25／jieba（ADR-009 契約），所有 rag import
#   都在函式內。
# - **不會卡住呼叫端**：檢索與 LLM 各有逾時；任何一段失敗都降級為「照常回答
#   但沒有文件脈絡」或回傳帶 ``error`` 的收據，呼叫端永遠拿得到東西。
# - **收據誠實**：回傳值說明有沒有用到記憶區（幾筆、幾個字）、有沒有檢索到
#   文件（幾則引用）、用了哪個 provider／model。



logger = logging.getLogger("OmniContext.SecretaryAsk")

DEFAULT_ANSWER_TIMEOUT_SECONDS = 120
MAX_QUESTION_CHARS = 2000

ASK_CLAIM_BOUNDARY = (
    "答案由所選 LLM 依本機記憶區與知識庫切片生成；引用列出的是被檢索到的檔案，"
    "不代表答案的每一句都有出處。"
)


class AskRejected(ValueError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


def _run_async(coro: Any, timeout: float) -> Any:
    """在沒有事件迴圈的執行緒（如 Telegram poller）跑 async 串流。

    若呼叫端本身已在事件迴圈裡（例如 FastAPI 端點），改丟到獨立執行緒跑，
    避免 ``asyncio.run`` 在既有迴圈中拋錯。
    """

    async def _with_timeout() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_with_timeout())).result()


class _RetrievalRequest:
    """`rag.router._retrieve_citations` 只讀這四個欄位；用最小殼避免重寫檢索邏輯。"""

    def __init__(self, strategy: str | None, top_k: int | None, alpha: float | None, threshold: float):
        self.retrieval_strategy = strategy
        self.top_k = top_k
        self.hybrid_alpha = alpha
        self.score_threshold = threshold


def _answer_timeout(cfg: Any) -> int:
    try:
        value = int(cfg.get("secretary_ask.timeout_seconds", DEFAULT_ANSWER_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_ANSWER_TIMEOUT_SECONDS
    return max(10, min(value, 600))


def ask_secretary(
    question: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    enable_rag: bool = True,
    top_k: int | None = None,
    retrieval_strategy: str | None = None,
    cfg: Any | None = None,
    timeout_seconds: int | None = None,
    memory: dict[str, Any] | None = None,
    gateway: Any | None = None,
) -> dict[str, Any]:
    """問一句、拿一整段答案；永遠回傳收據，不對呼叫端拋 LLM 例外。"""
    cfg = cfg or get_config()
    question = (question or "").strip()
    if not question:
        raise AskRejected("empty_question", "問題不可為空")
    if len(question) > MAX_QUESTION_CHARS:
        raise AskRejected("question_too_long", f"問題不可超過 {MAX_QUESTION_CHARS} 字")

    started = time.perf_counter()
    receipt: dict[str, Any] = {
        "answer": "",
        "citations": [],
        "memory": {"included": False, "reason": "not_requested"},
        "provider": (provider or cfg.get("rag.active_provider", "ollama")),
        "model": model,
        "rag_used": False,
        "error": None,
        "claim_boundary": ASK_CLAIM_BOUNDARY,
    }

    # 1. 記憶區脈絡（ADR-012）：與網頁對話注入的完全相同
    memory_text = ""
    if memory is not None:
        memory_text = str(memory.get("text") or "")
        receipt["memory"] = memory.get("receipt") or {"included": bool(memory_text)}
    else:
        try:
            if chat_context_enabled(cfg):
                # 與網頁對話注入的完全相同：今日視圖與提案都帶上（ADR-024 組合點）
                built = full_memory_context(cfg=cfg)
                memory_text = built["text"]
                receipt["memory"] = built["receipt"]
            else:
                receipt["memory"] = {"included": False, "reason": "disabled"}
        except Exception as exc:  # noqa: BLE001 — 記憶區故障不得中止問答
            logger.warning("memory context unavailable for ask: %s", type(exc).__name__)
            receipt["memory"] = {"included": False, "reason": f"error:{type(exc).__name__}"}

    # 2. 知識庫檢索（沿用與網頁同一條路徑）
    context_text = ""
    if enable_rag:
        try:
            from rag.retrieval.context import format_context_prompt
            from rag.router import _retrieve_citations

            citations = _retrieve_citations(
                question,
                _RetrievalRequest(retrieval_strategy, top_k, None, 0.0),
            )
            context_text = format_context_prompt(citations)
            receipt["rag_used"] = bool(citations)
            receipt["citations"] = [
                {
                    "index": getattr(c, "index", None),
                    "filename": getattr(c, "filename", None) or getattr(c, "title", None),
                    "file_path": getattr(c, "file_path", None),
                    "score": getattr(c, "score", None),
                }
                for c in citations
            ]
        except Exception as exc:  # noqa: BLE001 — 檢索失敗照常回答
            logger.warning("retrieval unavailable for ask: %s", type(exc).__name__)
            receipt["citations"] = []
            receipt["rag_used"] = False
            receipt["retrieval_error"] = type(exc).__name__

    # 3. LLM：把串流收成完整字串
    from rag.config import rag_settings

    prompt_parts = [str(rag_settings.DEFAULT_SYSTEM_PROMPT)]
    if memory_text:
        prompt_parts.append(memory_text)
    if context_text and receipt["rag_used"]:
        prompt_parts.append(context_text)
    system_prompt = "\n\n".join(prompt_parts)

    if gateway is None:
        from core.llm_client import llm_client as gateway
    # 與知識庫分頁同一組預設（rag.active_provider／rag.active_model）；D2 之後由呼叫端決定
    from rag.router import _chat_target

    chat_provider, chat_model = _chat_target(provider, model)

    async def _collect() -> str:
        chunks: list[str] = []
        async for token in gateway.stream_chat(
            messages=[{"role": "user", "content": question}],
            system_prompt=system_prompt,
            provider=chat_provider,
            model=chat_model,
        ):
            chunks.append(token)
        return "".join(chunks)

    timeout = timeout_seconds or _answer_timeout(cfg)
    try:
        receipt["answer"] = _run_async(_collect(), timeout=timeout).strip()
    except asyncio.TimeoutError:
        receipt["error"] = "timeout"
        receipt["answer"] = f"（{timeout} 秒內沒有得到完整回答；請稍後再問，或改用本機 Ollama。）"
    except Exception as exc:  # noqa: BLE001 — provider 錯誤如實轉成可讀訊息
        logger.error("ask_secretary LLM call failed: %s", exc, exc_info=True)
        receipt["error"] = type(exc).__name__
        receipt["answer"] = f"（回答失敗：{type(exc).__name__}；詳見本機服務日誌。）"

    if not receipt["answer"]:
        receipt["answer"] = "（模型沒有回覆內容。）"
    receipt["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return receipt


# ---------------------------------------------------------------- 「01 今天」視圖（ADR-024）
#
# 原本在 secretary/packs.py，但它同時要讀排程狀態、執行器開關與記憶區——那是呈現層的
# 組合工作，不是早晨包的一部分；留在 packs 會讓 packs 反過來 import scheduled_tasks。

def build_today_view(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """儀表板「01 今天」用：上次做到哪＋早晨包摘要＋預設排程狀態。提案另由 /proposals 提供。"""

    cfg = cfg or get_config()
    now = now or get_local_now()
    if projects is None:
        from core.project_engine import get_active_projects_list

        projects = get_active_projects_list()
    top = projects[0] if projects else None
    resume = None
    if top:
        resume = {
            "project_key": top.get("project_key"),
            "display_name": top.get("display_name"),
            "category": top.get("category"),
            "last_activity_at": top.get("last_activity_at"),
            "last_action_summary": top.get("last_action_summary"),
            "open_loops_count": top.get("open_loops_count"),
            "local_path": top.get("local_path"),
            "github_url": top.get("github_url"),
        }
    pack = latest_pack_summary(database=database, now=now)
    try:
        presets = presets_status(database=database, now=now)
    except Exception as exc:  # noqa: BLE001 — 排程表讀不到也不該讓今日視圖消失
        presets = {"error": type(exc).__name__}
    calendar: dict[str, Any] = {"enabled": False, "count": 0, "line": None}
    try:
        from core.calendar_agenda import day_agenda, schedule_sentence

        agenda = day_agenda(now=now, database=database, cfg=cfg)
        calendar = {
            "enabled": agenda["enabled"],
            "count": agenda["count"],
            "remaining_count": agenda["remaining_count"],
            "ongoing": agenda["ongoing"],
            "next": agenda["next"],
            "line": schedule_sentence(agenda),
            "claim_boundary": agenda["claim_boundary"],
        }
    except Exception as exc:  # noqa: BLE001 — 行事曆讀不到也不該讓今日視圖消失
        calendar = {"enabled": False, "error": type(exc).__name__, "count": 0, "line": None}
    # ADR-022 D2：「在開會」只用行事曆事件 ＋ 前景應用程式名稱兩個確定性訊號。
    meeting: dict[str, Any] = {"enabled": False, "in_meeting": False, "line": None}
    try:
        from core.meeting_transcripts import meeting_context

        meeting = meeting_context(database=database, cfg=cfg, now=now)
    except Exception as exc:  # noqa: BLE001 — 會議訊號讀不到也不該讓今日視圖消失
        meeting = {"enabled": False, "in_meeting": False, "line": None, "error": type(exc).__name__}
    memory: dict[str, Any] = {"enabled": False, "counts": {}, "total": 0}
    try:
        from core.secretary.memory import list_notes, memory_enabled

        if memory_enabled(cfg):
            listed = list_notes(limit=1, database=database)
            memory = {"enabled": True, "counts": listed["counts"], "total": listed["total"]}
    except Exception as exc:  # noqa: BLE001 — 記憶區讀不到也不該讓今日視圖消失
        memory = {"enabled": False, "error": type(exc).__name__, "counts": {}, "total": 0}
    return {
        "generated_at": (now.replace(tzinfo=None) if now.tzinfo else now).isoformat(timespec="seconds"),
        "resume": resume,
        "active_project_count": sum(1 for p in projects if p.get("status") == "active"),
        "pack": pack,
        "pack_line": pack_summary_line(pack),
        "memory": memory,
        "calendar": calendar,
        "meeting": meeting,
        "schedules": {
            "executor_enabled": executor_enabled(cfg),
            "scheduled_tasks_enabled": scheduled_tasks_enabled(cfg),
            # 設定檔還留著已淘汰的 scheduled_tasks.enabled: false 時，UI 要講得出
            # 「為什麼開了執行器還是沒排程」（TODO D6）。
            "scheduled_tasks_legacy_opt_out": legacy_opt_out(cfg),
            "l2_enabled": l2_enabled(cfg),
            **presets,
        },
        "claim_boundary": "只彙整既有唯讀資料（專案狀態、最近一次早晨包收據、排程表）；不執行任何動作。",
    }


def full_memory_context(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """記憶脈絡的**組合點**（ADR-024）：今日視圖與 top 提案在這一層取得，再注入記憶層。

    記憶層（`core/secretary/memory.py`）不准 import 呈現層或聚合層——D8 之前它為了在
    脈絡裡塞一行早晨包摘要與三個提案，反過來 import 了 packs 與 aggregate，那正是
    `proactive_secretary ↔ secretary_memory ↔ secretary_home` 那個環的一段。

    任何一段拿不到就少那一段（如實記在 receipt 的 sections 裡），筆記照常注入。
    """
    today = None
    try:
        today = build_today_view(database=database, cfg=cfg, now=now)
    except Exception as exc:  # noqa: BLE001 — 今日視圖壞了也要能給筆記
        logger.warning("today view unavailable for memory context: %s", type(exc).__name__)
    proposals: list[dict[str, Any]] = []
    try:
        from core.secretary.aggregate import build_action_proposals

        proposals = build_action_proposals(database=database, cfg=cfg, now=now, limit=3).get("proposals", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposals unavailable for memory context: %s", type(exc).__name__)
    return memory_context(
        database=database, cfg=cfg, now=now, today=today, proposals=proposals, max_chars=max_chars
    )
