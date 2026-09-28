"""七個 tool：schema、白名單投影、dispatch、selftest。零 SDK 依賴。

**白名單寫死在這裡**（ADR-032 決策四）：沒列在名單上的鍵一律不出現在輸出裡。
新增欄位＝改這份名單＝會被 code review 看到。`readers` 已經先投影過一次，這裡是
第二道同型的閘門——重複是刻意的，因為漏掉的後果是把使用者名稱送進別人的 agent。

**錯誤訊息只能來自 `ERRORS` 這張封閉字串表，永遠不得含 `str(exc)`**（ADR-032 D4）：
白名單投影管的是**回傳值**，例外訊息是從旁邊出去的。已查證的一個洩漏面是
`core/semantic_index.py:77-80` 會把 Ollama 回應 body 的前 500 字塞進 `RuntimeError`。

**selftest 放這裡而不是 `server.py`**：它只需要 readers 與投影，不需要 SDK，所以在
沒裝 `[mcp]` 的核心安裝裡也跑得完——唯讀證明因此能在 CI 的
`test-core-without-rag-extra` job 裡實際執行，而不是只存在於有 extra 的環境。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mcpserver import availability, readers, receipts

SCHEMA_VERSION = "1"

CLAIM_BOUNDARY = (
    "唯讀快照：每一筆都帶 source_ref 指回 SQLite row。"
    "時間鄰近與相似度都不構成因果，未被採集到的活動不會出現在這裡。"
)

# 封閉的錯誤字串表。代碼進 receipt，訊息給人看；兩者都不得由例外內容拼出來。
ERRORS: Dict[str, str] = {
    "database_missing": "找不到資料庫。請先讓主服務至少跑過一次（python main.py run）。",
    "schema_unmigrated": "資料庫 schema 落後於程式碼。MCP 是唯讀的、不會自己升級——請跑一次主服務讓它套用 migration。",
    "mcp_disabled": "MCP Context Server 預設關閉。要啟用請在設定檔把 mcp.enabled 設為 true。",
    "mcp_extra_not_installed": f"MCP server 需要選用依賴。請執行 {availability.INSTALL_HINT} 後重試。",
    "unknown_tool": "沒有這個 tool。",
    "invalid_argument": "參數不合規格。",
    "read_failed": "讀取失敗。",
    # 檢索用的是本機 Ollama 的 /api/embed。打不到就明說打不到——**不 fallback 到雲端**
    # （ADR-023），也**不回空陣列冒充「沒有結果」**：那兩種都是拿「查不到」冒充「沒發生」。
    "ollama_unreachable": "打不到本機 Ollama，語意檢索無法進行。請確認 ollama 正在執行（ollama serve），然後重試。",
}

# ---- 輸出白名單 ---------------------------------------------------------------

PROJECT_FIELDS: Tuple[str, ...] = (
    "source_ref", "source_ref_token", "project_key", "display_name", "category", "status",
    "last_activity_at", "idle_days", "open_loops_open_count", "repo", "git",
)
REPO_FIELDS: Tuple[str, ...] = ("name", "has_local_path", "github_url")
GIT_FIELDS: Tuple[str, ...] = ("last_fetch_at",)

HANDOFF_FIELDS: Tuple[str, ...] = (
    "project_key", "display_name", "status", "idle_days", "last_activity_at",
    "open_loops", "recent_commits", "recent_files", "recent_ai_turns", "markdown",
)
OPEN_LOOP_FIELDS: Tuple[str, ...] = (
    "source_ref", "source_ref_token", "project_key", "status", "source_type", "confidence",
    "fingerprint", "created_at", "last_seen_at",
)
COMMIT_FIELDS: Tuple[str, ...] = ("source_ref", "source_ref_token", "hash", "message", "branch", "changed_at")
FILE_FIELDS: Tuple[str, ...] = ("source_ref", "source_ref_token", "name", "changed_at")
AI_TURN_FIELDS: Tuple[str, ...] = (
    "source_ref", "source_ref_token", "platform", "time", "turn_key", "response_status",
    "prompt_excerpt", "response_excerpt",
)

SEARCH_SOURCE_FIELDS: Tuple[str, ...] = (
    "citation", "source_ref", "source_ref_token", "source_type", "project_key",
    "trust_status", "score", "source_updated_at", "title", "excerpt",
)
SESSION_FIELDS: Tuple[str, ...] = (
    "session_id", "project_key", "started_at", "ended_at", "span_minutes",
    "events_observed", "event_counts", "source_types", "inference_status",
    "headline", "narrative", "items",
)
SESSION_ITEM_FIELDS: Tuple[str, ...] = (
    "source_ref", "source_ref_token", "timestamp", "event_type", "trust_status", "channel", "title",
)
DIGEST_NOTE_FIELDS: Tuple[str, ...] = (
    "source_ref", "source_ref_token", "project_key", "created_at", "title", "body",
)

# **內容鍵**（不是「節錄鍵」）。E3 只認三個節錄欄位，E4 發現那條線畫錯了：
# `title` 與 `body` 一樣是使用者寫的字——`open_loops.title` 是從 AI 對話抽出來的
# （決策四正是為此不回它），`secretary_notes.title/body` 是使用者自己打的，
# `semantic_documents.title` 對 open_loop 來源直接就是那個標題，
# work session 的 `headline`／`narrative`／`items[].title` 則含 prompt 前 140 字
# （core/context_memory.py:108）。把它們當 metadata，`mcp.metadata_only: true` 就會
# 一邊宣稱「只回 metadata 不回任何內容節錄」一邊把標題送出去。
#
# 所以這條線改成：**內容 = 使用者寫的字**，其餘才是 metadata。`metadata_only: true`
# 時這些鍵一個都不出現，D4 的掃描面也是「拿掉這些鍵之後剩下的」。
# 定義住在 `readers`（`tools` import `readers`，反過來會成環），這裡只是別名——
# 兩份名單會漂移，而漂移的症狀是某個內容欄位靜悄悄變成 metadata。
CONTENT_KEYS: Tuple[str, ...] = readers.CONTENT_KEYS

TOOLS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "omni_project_state",
        "description": (
            "列出 canonical 專案狀態（唯讀）。回的是主服務上次算出來的快照，"
            "state_recorded_at 說明那是什麼時候記下的；MCP 不會替你重算。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "maxLength": 255},
                "status": {"type": "string", "enum": ["active", "idle", "stale"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "omni_handoff",
        "description": (
            "某個專案的接續脈絡：未結事項、最近 commit／檔案／AI 對話，外加一份 markdown。"
            "所有本機絕對路徑都換成 source_ref；未結事項不回標題。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "maxLength": 255},
                "turns": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["project"],
            "additionalProperties": False,
        },
    },
    {
        "name": "omni_search_history",
        "description": (
            "在本機語意索引裡找相關的歷史紀錄（唯讀、retrieval-only）。"
            "**不做合成**：只回找得到的證據與 source_ref，要下結論是你的事。"
            "需要本機 Ollama；打不到就明說打不到，不會改用雲端、也不會回空陣列冒充沒有結果。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 2000},
                "project": {"type": "string", "maxLength": 255},
                "since": {"type": "string", "maxLength": 32,
                          "description": "ISO 日期。注意：這是排序**後**的過濾，不是「該時段內最相關的 n 筆」。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "omni_open_loops",
        "description": (
            "未結事項清單（唯讀）。預設只回 open；要 stale／resolved／superseded 必須明講。"
            "**不回標題**——未結事項的標題是從 AI 對話抽出來的，可能含你貼進去的原文。"
            "要標題請拿該筆的 source_ref ＋ source_ref_token 呼叫 omni_resolve_ref。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "maxLength": 255},
                "status": {"type": "string", "enum": list(readers.OPEN_LOOP_STATUSES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "omni_work_sessions",
        "description": (
            "最近的工作階段（唯讀）。session 是以專案 ＋ 停頓間隔做的**時間推論**，"
            "不是實際任務真相，也不能拿來推論工時或成果。"
            "前景視窗事件因為缺少歸戶不納入，這件事會寫在 coverage.excluded 裡。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "maxLength": 255},
                "hours": {"type": "integer", "minimum": 1, "maximum": 2160},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "omni_recent_digest",
        "description": (
            "已經寫下的工作誌（date）或週回顧（weeks_back），唯讀。"
            "**不會即時產生**：沒有觀察就回 observed: false，那代表採集器沒看到東西，"
            "不代表你那天沒工作。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "maxLength": 32},
                "weeks_back": {"type": "integer", "minimum": 0, "maximum": 12},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "omni_resolve_ref",
        "description": (
            "把 source_ref 展開成一列（唯讀）。**三態**：ok／stale_gone／stale_reused。"
            "不給 source_ref_token 時只會回 ok ＋ verified: false——因為主鍵是 rowid 別名，"
            "刪掉之後會被重用，裸指標分不出「還是原來那一列」與「那個位置換人了」。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_ref": {"type": "string", "maxLength": 300},
                "source_ref_token": {"type": "string", "maxLength": 64},
            },
            "required": ["source_ref"],
            "additionalProperties": False,
        },
    },
)

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)


class ToolError(RuntimeError):
    """帶封閉代碼的錯誤；`message` 一律取自 `ERRORS`。"""

    def __init__(self, code: str):
        self.code = code if code in ERRORS else "read_failed"
        super().__init__(ERRORS[self.code])


def _project(payload: Mapping[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    """白名單投影：只留名單上的鍵，其餘一律丟掉。"""
    return {key: payload[key] for key in fields if key in payload}


def _envelope(payload: Dict[str, Any], *, now: Any = None) -> Dict[str, Any]:
    from core.time_utils import get_local_now

    payload = dict(payload)
    payload["schema_version"] = SCHEMA_VERSION
    payload["generated_at"] = (now or get_local_now()).isoformat(timespec="seconds")
    payload["claim_boundary"] = CLAIM_BOUNDARY
    return payload


def _require_ready(db_path: Optional[Path]) -> None:
    state = readers.schema_state(db_path)
    if state != "ok":
        raise ToolError(state)


# ---- 各 tool ------------------------------------------------------------------


def omni_project_state(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    unknown = set(args) - {"project", "status", "limit"}
    if unknown:
        raise ToolError("invalid_argument")
    status = args.get("status")
    if status is not None and status not in {"active", "idle", "stale"}:
        raise ToolError("invalid_argument")
    _require_ready(db_path)
    try:
        raw = readers.project_state(
            project=args.get("project"),
            status=status,
            limit=int(args.get("limit") or 20),
            db_path=db_path,
            now=now,
        )
    except readers.ReaderUnavailable as exc:
        raise ToolError(exc.code) from None
    except Exception:  # 例外內容永遠不外流（D4）
        raise ToolError("read_failed") from None

    projects: List[Dict[str, Any]] = []
    for item in raw["projects"]:
        row = _project(item, PROJECT_FIELDS)
        if isinstance(row.get("repo"), Mapping):
            row["repo"] = _project(row["repo"], REPO_FIELDS)
        if isinstance(row.get("git"), Mapping):
            row["git"] = _project(row["git"], GIT_FIELDS)
        projects.append(row)
    return _envelope(
        {
            "tool": "omni_project_state",
            "result": "ok" if projects else "empty",
            "projects": projects,
            "state_recorded_at": raw.get("state_recorded_at"),
            "next_step": None
            if projects
            else "資料庫裡還沒有專案狀態。先讓主服務跑一次（python main.py run）把採集器打開。",
        },
        now=now,
    )


def omni_handoff(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    unknown = set(args) - {"project", "turns"}
    if unknown or not str(args.get("project") or "").strip():
        raise ToolError("invalid_argument")
    _require_ready(db_path)
    include_excerpts = not availability.metadata_only()
    try:
        raw = readers.handoff(
            project=str(args["project"]).strip(),
            turns=int(args.get("turns") or 5),
            include_excerpts=include_excerpts,
            db_path=db_path,
            now=now,
        )
    except readers.ReaderUnavailable as exc:
        raise ToolError(exc.code) from None
    except Exception:
        raise ToolError("read_failed") from None

    payload = _project(raw, HANDOFF_FIELDS)
    payload["open_loops"] = [_project(x, OPEN_LOOP_FIELDS) for x in raw.get("open_loops", [])]
    payload["recent_commits"] = [_project(x, COMMIT_FIELDS) for x in raw.get("recent_commits", [])]
    payload["recent_files"] = [_project(x, FILE_FIELDS) for x in raw.get("recent_files", [])]
    payload["recent_ai_turns"] = [_project(x, AI_TURN_FIELDS) for x in raw.get("recent_ai_turns", [])]
    found = any(
        payload.get(key) for key in ("open_loops", "recent_commits", "recent_files", "recent_ai_turns")
    )
    payload["tool"] = "omni_handoff"
    payload["result"] = "ok" if found else "empty"
    payload["excerpts_enabled"] = include_excerpts
    payload["content_included"] = include_excerpts
    if not include_excerpts:
        payload = _strip_content(payload)
    payload["next_step"] = None if found else (
        "這個專案在資料庫裡還沒有可追溯的活動。確認專案名稱，"
        "或先讓主服務跑一段時間把採集器的資料累積起來。"
    )
    return _envelope(payload, now=now)


def _content_enabled() -> bool:
    """`mcp.metadata_only` 的唯一讀取點。true ＝ 一個內容鍵都不出去。"""
    return not availability.metadata_only()


def _strip_content(node: Any) -> Any:
    """把內容鍵整個拿掉（不是留空字串——留空字串會讓呼叫端以為那筆沒內容）。"""
    if isinstance(node, Mapping):
        return {k: _strip_content(v) for k, v in node.items() if k not in CONTENT_KEYS}
    if isinstance(node, (list, tuple)):
        return [_strip_content(item) for item in node]
    return node


def omni_search_history(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    if set(args) - {"query", "project", "since", "limit"}:
        raise ToolError("invalid_argument")
    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        raise ToolError("invalid_argument")
    _require_ready(db_path)
    include_content = _content_enabled()
    try:
        raw = readers.search_history(
            query=query,
            project=args.get("project"),
            since=args.get("since"),
            limit=int(args.get("limit") or 6),
            include_content=include_content,
            db_path=db_path,
        )
    except readers.ReaderUnavailable as exc:
        # `ollama_unreachable` 不是「沒找到」——它要以 unavailable 的形狀回去，
        # 而且**不能**退化成空的 sources（那是拿查不到冒充沒發生）。
        if exc.code == "ollama_unreachable":
            return _envelope(
                {
                    "tool": "omni_search_history",
                    "result": "unavailable",
                    "status": "unavailable",
                    "reason": "ollama_unreachable",
                    "sources": None,
                    "next_step": ERRORS["ollama_unreachable"],
                },
                now=now,
            )
        raise ToolError(exc.code) from None
    except Exception:
        raise ToolError("read_failed") from None

    tokens = readers.ref_tokens_for([x["source_ref"] for x in raw["sources"]], db_path)
    sources: List[Dict[str, Any]] = []
    for item in raw["sources"]:
        row = dict(item)
        row["source_ref_token"] = tokens.get(row["source_ref"])
        sources.append(_project(row, SEARCH_SOURCE_FIELDS))
    payload = {
        "tool": "omni_search_history",
        "result": "ok" if sources else "empty",
        "status": "retrieved",
        "sources": sources,
        "embedding_model": raw.get("embedding_model"),
        "indexed_candidates": raw.get("indexed_candidates", 0),
        "since_applied": raw.get("since_applied"),
        "truncated_by_since": raw.get("truncated_by_since", 0),
        "retrieval_claim_boundary": raw.get("retrieval_claim_boundary"),
        "content_included": include_content,
        "next_step": None,
    }
    # **順序就是語意**：`since` 砍掉東西這件事要先講。第一版把「空手而回」排在前面，
    # 於是 since 把全部結果濾光時會回「都不夠相近，換個說法再試」——那是把
    # 「我自己濾掉了」講成「沒有這種東西」，呼叫端會一直換問法而永遠問不到。
    # 契約測試 test_search_history_marks_since_as_a_post_rank_filter 就是在守這一列的位置。
    if raw.get("truncated_by_since"):
        payload["next_step"] = (
            f"since 砍掉了 {raw['truncated_by_since']} 筆已排序的結果"
            + ("（所以這次一筆都沒剩）。" if not sources else "。")
            + "since 是排序**後**的過濾，所以這不是「該時段內最相關的 n 筆」；"
            "要那個結果請放寬 since 並自行依時間篩選。"
        )
    elif not sources:
        payload["next_step"] = (
            "語意索引裡沒有相符的紀錄。可能是索引還沒建（到儀表板建一次索引），"
            "或這個問法在本機證據裡沒有對應——換個說法再試，或用 omni_work_sessions 看時間軸。"
            if raw.get("indexed_candidates", 0) == 0
            else f"索引裡有 {raw.get('indexed_candidates', 0)} 筆候選，但都不夠相近。換個說法或放寬 project／since 再試。"
        )
    return _envelope(payload, now=now)


def omni_open_loops(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    if set(args) - {"project", "status", "limit"}:
        raise ToolError("invalid_argument")
    status = str(args.get("status") or "open")
    if status not in readers.OPEN_LOOP_STATUSES:
        # `get_open_loops_list()` 對白名單外的 status 會 raise ValueError；
        # traceback 不得穿過 stdio，所以在這裡就換成封閉字串表的代碼。
        raise ToolError("invalid_argument")
    _require_ready(db_path)
    try:
        raw = readers.open_loops(
            project=args.get("project"),
            status=status,
            limit=int(args.get("limit") or 50),
            db_path=db_path,
        )
    except readers.ReaderUnavailable as exc:
        raise ToolError(exc.code) from None
    except Exception:
        raise ToolError("read_failed") from None

    loops = [_project(item, OPEN_LOOP_FIELDS) for item in raw["loops"]]
    return _envelope(
        {
            "tool": "omni_open_loops",
            "result": "ok" if loops else "empty",
            "loops": loops,
            "status_filter": status,
            "titles_withheld": True,
            "next_step": None
            if loops
            else (
                f"沒有 status={status} 的未結事項。"
                + ("預設只看 open；要看沉睡中的請帶 status='stale'。" if status == "open" else "")
            ),
        },
        now=now,
    )


def omni_work_sessions(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    if set(args) - {"project", "hours", "limit"}:
        raise ToolError("invalid_argument")
    _require_ready(db_path)
    include_content = _content_enabled()
    try:
        raw = readers.work_sessions(
            project=args.get("project"),
            hours=int(args.get("hours") or 72),
            limit=int(args.get("limit") or 8),
            db_path=db_path,
            now=now,
        )
    except readers.ReaderUnavailable as exc:
        raise ToolError(exc.code) from None
    except Exception:
        raise ToolError("read_failed") from None

    refs = [
        item["source_ref"]
        for session in raw.get("sessions", [])
        for item in session.get("items", [])
        if item.get("source_ref")
    ]
    tokens = readers.ref_tokens_for(refs, db_path)
    sessions: List[Dict[str, Any]] = []
    for session in raw.get("sessions", []):
        row = _project(session, SESSION_FIELDS)
        # 既有實作會在每個 session 上掛最多 3 筆帶標題的 open loop
        # （core/context_memory.py:244）。白名單沒有 open_loops 這個鍵，所以它在
        # `_project()` 那一步就掉了——這裡再斷言一次，是因為靜悄悄掉比噴錯難查。
        assert "open_loops" not in row, "work session 不得附掛未結事項"
        items = []
        for item in session.get("items", []):
            entry = dict(item)
            entry["source_ref_token"] = tokens.get(entry.get("source_ref"))
            items.append(_project(entry, SESSION_ITEM_FIELDS))
        row["items"] = items
        sessions.append(row)

    if not include_content:
        sessions = _strip_content(sessions)

    return _envelope(
        {
            "tool": "omni_work_sessions",
            "result": "ok" if sessions else "empty",
            "sessions": sessions,
            "window": {
                "hours": raw.get("window_hours"),
                "gap_minutes": raw.get("gap_minutes"),
                # 時間窗語意要標示：collect_work_observations() 用閉區間
                # since <= ts <= until，而 activity_sources.day_bounds() 用半開區間。
                # 兩個 tool 的「今天」不是同一個今天，不要讓呼叫端以為是。
                "bounds": "closed",
            },
            "observations_considered": raw.get("observations_considered", 0),
            "coverage": raw.get("coverage"),
            "content_included": include_content,
            "session_claim_boundary": raw.get("claim_boundary"),
            "next_step": None
            if sessions
            else (
                f"這 {raw.get('window_hours')} 小時內沒有已歸戶的活動。"
                "把 hours 調大，或先確認主服務有在跑（前景視窗事件因為缺少歸戶本來就不算）。"
            ),
        },
        now=now,
    )


def omni_recent_digest(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    if set(args) - {"date", "weeks_back", "limit"}:
        raise ToolError("invalid_argument")
    if args.get("date") is not None and args.get("weeks_back") is not None:
        raise ToolError("invalid_argument")  # 兩者互斥
    _require_ready(db_path)
    include_content = _content_enabled()
    weekly = args.get("weeks_back") is not None
    try:
        raw = readers.recent_digest(
            date=args.get("date"),
            weeks_back=int(args["weeks_back"]) if weekly else None,
            limit=int(args.get("limit") or 20),
            include_content=include_content,
            db_path=db_path,
            now=now,
        )
    except readers.ReaderUnavailable as exc:
        raise ToolError(exc.code) from None
    except Exception:
        raise ToolError("read_failed") from None

    notes = [_project(item, DIGEST_NOTE_FIELDS) for item in raw["notes"]]
    observed = bool(notes)
    payload: Dict[str, Any] = {
        "tool": "omni_recent_digest",
        "result": "ok" if observed else "empty",
        # 結構化旗標是刻意的：一句「該日無觀察」很容易被讀成「使用者那天沒工作」，
        # 而實情只是採集器那天沒看到東西。中文句子留給人看，判斷用這個布林。
        "status": "found" if observed else "no_observation",
        "observed": observed,
        "notes": notes,
        "content_included": include_content,
        "generated_on_demand": False,
        "next_step": None,
    }
    for key in ("date", "period", "period_from", "period_to"):
        if key in raw:
            payload[key] = raw[key]
    if not observed:
        payload["next_step"] = (
            "那一週還沒有回顧筆記。週回顧只會對**已結束**的週產生，weeks_back=0 指的是"
            "進行中的這一週，所以通常查不到；要上一週請用 weeks_back=1。"
            if weekly and int(args["weeks_back"]) <= 0
            else (
                "那段期間沒有已寫下的觀察。這代表採集器沒看到東西或秘書記憶區關著，"
                "**不代表那天沒工作**。MCP 是唯讀的，不會替你即時產生一份。"
            )
        )
    return _envelope(payload, now=now)


def omni_resolve_ref(
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    if set(args) - {"source_ref", "source_ref_token"}:
        raise ToolError("invalid_argument")
    ref = str(args.get("source_ref") or "").strip()
    if not ref:
        raise ToolError("invalid_argument")
    _require_ready(db_path)
    include_content = _content_enabled()
    try:
        outcome = readers.resolve_ref(ref, args.get("source_ref_token"), db_path)
    except readers.ReaderUnavailable as exc:
        raise ToolError(exc.code) from None
    except Exception:
        raise ToolError("read_failed") from None

    if outcome["status"] == "invalid_ref":
        raise ToolError("invalid_argument")

    table = str(outcome["source_ref"]).split(":", 1)[0]
    row = outcome.get("row")
    payload: Dict[str, Any] = {
        "tool": "omni_resolve_ref",
        "result": "ok" if outcome["status"] == "ok" else "stale",
        "status": outcome["status"],
        "source_ref": outcome["source_ref"],
        "source_ref_token": outcome.get("source_ref_token"),
        "verified": outcome["verified"],
        "table": table,
        "content_included": include_content,
        "row": readers.expand_row(table, row, include_content=include_content) if row else None,
        "next_step": None,
    }
    if outcome["status"] == "stale_gone":
        payload["next_step"] = (
            "那一列已經不在資料庫裡了（被刪除或被資料保存政策清掉）。"
            "手上的摘要別再當成現況；用 omni_search_history 或 omni_work_sessions 重新找一次。"
        )
    elif outcome["status"] == "stale_reused":
        payload["next_step"] = (
            "那個 id 現在是**另一列**了——本專案的主鍵是 rowid 別名，刪掉之後會被重用。"
            "所以這不是你當初拿到的那筆，內容刻意不回。請重新查詢取得新的 source_ref。"
        )
    elif not outcome["verified"]:
        payload["next_step"] = (
            "查得到，但你沒給 source_ref_token，所以**無法確認這還是當初那一列**"
            "（id 會在刪除後被重用）。下次把 tool 回傳的 source_ref_token 一起帶上。"
        )
    elif not include_content:
        payload["next_step"] = "mcp.metadata_only 目前是 true，所以只回 metadata。要內容請把它設成 false。"
    return _envelope(payload, now=now)


DISPATCH = {
    "omni_project_state": omni_project_state,
    "omni_handoff": omni_handoff,
    "omni_search_history": omni_search_history,
    "omni_open_loops": omni_open_loops,
    "omni_work_sessions": omni_work_sessions,
    "omni_recent_digest": omni_recent_digest,
    "omni_resolve_ref": omni_resolve_ref,
}


def call_tool(
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    now: Any = None,
    enforce_gate: bool = True,
) -> Dict[str, Any]:
    """跑一個 tool 並留一筆收據。**每次呼叫前重問一次設定**（決策六）。"""
    started = time.perf_counter()
    handler = DISPATCH.get(name)
    if handler is None:
        receipts.write_receipt(
            tool=str(name), ok=False, result_count=0,
            elapsed_ms=int((time.perf_counter() - started) * 1000), error_code="unknown_tool",
        )
        raise ToolError("unknown_tool")
    if enforce_gate and not availability.mcp_enabled():
        receipts.write_receipt(
            tool=name, ok=False, result_count=0,
            elapsed_ms=int((time.perf_counter() - started) * 1000), error_code="mcp_disabled",
        )
        raise ToolError("mcp_disabled")
    try:
        payload = handler(arguments, db_path=db_path, now=now)
    except ToolError as exc:
        receipts.write_receipt(
            tool=name, ok=False, result_count=0,
            elapsed_ms=int((time.perf_counter() - started) * 1000), error_code=exc.code,
        )
        raise
    # **`unavailable` 不是 ok。** 第一版這裡一律寫 ok=true，於是
    # `omni_search_history` 在 Ollama 打不到時留下一筆「ok、result_count: 0」——
    # 收據自己在做 TODO E4 明令禁止的那件事：拿空結果冒充「沒發生」。
    # 回傳值那一層已經誠實（status: unavailable ＋ reason），收據不能比它鬆。
    unavailable = payload.get("result") == "unavailable"
    receipts.write_receipt(
        tool=name,
        ok=not unavailable,
        result_count=_result_count(payload),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        error_code=str(payload.get("reason")) if unavailable else None,
    )
    return payload


# 每個 tool 用哪個鍵來數「回了幾筆」。**寫死成一張表**而不是「掃所有 list 欄位」：
# 後者會把 omni_handoff 的四個清單加總成一個沒有意義的數字，也會在新增欄位時悄悄改變
# 收據的語意。收據是要拿來對帳的，語意不能浮動。
RESULT_COUNT_KEYS: Dict[str, Tuple[str, ...]] = {
    "omni_project_state": ("projects",),
    "omni_handoff": ("open_loops", "recent_commits", "recent_files", "recent_ai_turns"),
    "omni_search_history": ("sources",),
    "omni_open_loops": ("loops",),
    "omni_work_sessions": ("sessions",),
    "omni_recent_digest": ("notes",),
    "omni_resolve_ref": (),
}


def _result_count(payload: Mapping[str, Any]) -> int:
    keys = RESULT_COUNT_KEYS.get(str(payload.get("tool") or ""))
    if keys is None:
        return 0
    if not keys:  # omni_resolve_ref：解得開算 1，解不開算 0
        return 1 if payload.get("status") == "ok" else 0
    return sum(len(payload.get(key) or []) for key in keys)


# ---- selftest -----------------------------------------------------------------

# D4 第二道防線：白名單投影是實作，這張表只負責**驗證**。靠 regex 去刪東西等於承認
# 我們不知道自己在送什麼。
FORBIDDEN_OUTPUT = (
    re.compile(r"/home/[^/\s\"]+"),
    re.compile(r"/Users/[^/\s\"]+"),
    re.compile(r"/root/"),
    re.compile(r"[A-Za-z]:\\\\?Users\\\\?"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{10,}"),
)


def scan_output(text: str) -> List[str]:
    """回傳命中的樣式；空清單＝乾淨。"""
    return [pattern.pattern for pattern in FORBIDDEN_OUTPUT if pattern.search(text)]


def selftest(*, db_path: Path | None = None, enforce_gate: bool = True) -> Dict[str, Any]:
    """**七個 tool 各跑一次**，並產出唯讀證明（A23／A25／A26 的機器收據）。

    收據包含：每張表的 ``(列數, 內容雜湊)`` 前後比對、每筆 `source_ref` 回查得到、
    輸出通過禁用樣式掃描。**只比列數不算數**——ADR-032 Context 陷阱 4 已經實測證明
    UPSERT 會讓列數不動而內容改變。

    `omni_search_history` 需要本機 Ollama。打不到時它回 `unavailable`，selftest 把它
    記成 `skipped`（不是 failed）——**沒有 Ollama 不代表 MCP 壞了**，但也不能默默當成
    通過，所以它在收據裡是一個看得見的欄位。
    """
    import json as _json

    path = Path(db_path) if db_path is not None else readers.database_path()
    if not path.is_file():
        return {"status": "unavailable", "error_code": "database_missing", "checks": {}}

    before = readers.database_contract(path)
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    skipped: Dict[str, str] = {}

    def _run(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            payload = call_tool(name, arguments, db_path=path, enforce_gate=enforce_gate)
        except ToolError as exc:
            errors[name] = exc.code
            return None
        if payload.get("result") == "unavailable":
            skipped[name] = str(payload.get("reason") or "unavailable")
            return None
        results[name] = payload
        return payload

    state = _run("omni_project_state", {"limit": 5})
    project = ((state or {}).get("projects") or [{}])[0].get("project_key")
    if project:
        _run("omni_handoff", {"project": project, "turns": 3})
    else:
        errors["omni_handoff"] = "no_project_to_hand_off"

    _run("omni_open_loops", {"status": "open", "limit": 20})
    _run("omni_work_sessions", {"hours": 24 * 30, "limit": 5})
    _run("omni_recent_digest", {"weeks_back": 1})
    # query 帶一個特殊標記：D6 的契約測試會斷言它不出現在收據檔案裡。
    _run("omni_search_history", {"query": "omnicontext selftest probe", "limit": 3})

    after = readers.database_contract(path)

    refs: List[str] = []
    for payload in results.values():
        _collect_refs(payload, refs)
    unresolved = [ref for ref in refs if readers.resolve_source_ref(ref, path) is None]

    # 第七個 tool 用**前六個真的發出來的指標**驗，不是自己造一個——這樣它驗到的是
    # 「我們發出去的指標展得開」，而不是「resolve_ref 這支函式會動」。
    resolve_report: Dict[str, Any] = {"checked": 0, "verified": 0, "stale": []}
    tokens = readers.ref_tokens_for(refs, path)
    for ref in refs[:25]:
        outcome = _run("omni_resolve_ref", {"source_ref": ref, "source_ref_token": tokens.get(ref)})
        if outcome is None:
            continue
        resolve_report["checked"] += 1
        if outcome.get("verified") and outcome.get("status") == "ok":
            resolve_report["verified"] += 1
        else:
            resolve_report["stale"].append({"source_ref": ref, "status": outcome.get("status")})

    rendered = _json.dumps(_without_excerpts(results), ensure_ascii=False, default=str)

    checks = {
        "read_only_contract_unchanged": before == after,
        "tables_checked": len(before),
        "source_refs_seen": len(refs),
        "source_refs_unresolved": unresolved,
        "forbidden_output_hits": scan_output(rendered),
        "tools_answered": sorted(results),
        "tools_failed": errors,
        "tools_skipped": skipped,
        "refs_resolved": resolve_report,
    }
    ok = (
        checks["read_only_contract_unchanged"]
        and not unresolved
        and not checks["forbidden_output_hits"]
        and not resolve_report["stale"]
        and "omni_project_state" in results
    )
    return {"status": "passed" if ok else "failed", "checks": checks}


EXCERPT_KEYS = CONTENT_KEYS


def _without_excerpts(node: Any) -> Any:
    """把使用者寫的字拿掉，剩下的就是 metadata 面（D4 的掃描面）。"""
    return _strip_content(node)


def _collect_refs(node: Any, out: List[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "source_ref" and isinstance(value, str):
                out.append(value)
            else:
                _collect_refs(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_refs(item, out)
