"""六個 tool 中的前兩個：schema、白名單投影、dispatch、selftest。零 SDK 依賴。

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
}

# ---- 輸出白名單 ---------------------------------------------------------------

PROJECT_FIELDS: Tuple[str, ...] = (
    "source_ref", "project_key", "display_name", "category", "status",
    "last_activity_at", "idle_days", "open_loops_open_count", "repo", "git",
)
REPO_FIELDS: Tuple[str, ...] = ("name", "has_local_path", "github_url")
GIT_FIELDS: Tuple[str, ...] = ("last_fetch_at",)

HANDOFF_FIELDS: Tuple[str, ...] = (
    "project_key", "display_name", "status", "idle_days", "last_activity_at",
    "open_loops", "recent_commits", "recent_files", "recent_ai_turns", "markdown",
)
OPEN_LOOP_FIELDS: Tuple[str, ...] = (
    "source_ref", "project_key", "status", "source_type", "confidence",
    "fingerprint", "created_at", "last_seen_at",
)
COMMIT_FIELDS: Tuple[str, ...] = ("source_ref", "hash", "message", "branch", "changed_at")
FILE_FIELDS: Tuple[str, ...] = ("source_ref", "name", "changed_at")
AI_TURN_FIELDS: Tuple[str, ...] = (
    "source_ref", "platform", "time", "turn_key", "response_status",
    "prompt_excerpt", "response_excerpt",
)

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
    payload["next_step"] = None if found else (
        "這個專案在資料庫裡還沒有可追溯的活動。確認專案名稱，"
        "或先讓主服務跑一段時間把採集器的資料累積起來。"
    )
    return _envelope(payload, now=now)


DISPATCH = {"omni_project_state": omni_project_state, "omni_handoff": omni_handoff}


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
    receipts.write_receipt(
        tool=name, ok=True, result_count=_result_count(payload),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
    return payload


def _result_count(payload: Mapping[str, Any]) -> int:
    if "projects" in payload:
        return len(payload["projects"])
    return sum(
        len(payload.get(key) or [])
        for key in ("open_loops", "recent_commits", "recent_files", "recent_ai_turns")
    )


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
    """兩個 tool 各跑一次，並產出唯讀證明。

    收據包含：每張表的 ``(列數, 內容雜湊)`` 前後比對、每筆 `source_ref` 回查得到、
    輸出通過禁用樣式掃描。**只比列數不算數**——ADR-032 Context 陷阱 4 已經實測證明
    UPSERT 會讓列數不動而內容改變。
    """
    import json as _json

    path = Path(db_path) if db_path is not None else readers.database_path()
    if not path.is_file():
        return {"status": "unavailable", "error_code": "database_missing", "checks": {}}

    before = readers.database_contract(path)
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    state = call_tool("omni_project_state", {"limit": 5}, db_path=path, enforce_gate=enforce_gate)
    results["omni_project_state"] = state
    project = (state.get("projects") or [{}])[0].get("project_key")
    if project:
        try:
            results["omni_handoff"] = call_tool(
                "omni_handoff", {"project": project, "turns": 3},
                db_path=path, enforce_gate=enforce_gate,
            )
        except ToolError as exc:
            errors["omni_handoff"] = exc.code
    else:
        errors["omni_handoff"] = "no_project_to_hand_off"

    after = readers.database_contract(path)

    refs: List[str] = []
    for payload in results.values():
        _collect_refs(payload, refs)
    unresolved = [ref for ref in refs if readers.resolve_source_ref(ref, path) is None]
    # 掃的是**metadata 面**，不是內容節錄。這個區別 E3 實作時才浮出來，值得寫清楚：
    # 節錄是使用者 opt-in 的自己的內容，裡面出現 `/home/...` 是家常便飯（「我在那個目錄
    # 底下改了什麼」本來就是脈絡）。把節錄一起掃，這條規則會對合法資料永遠紅；
    # 不掃 metadata 面，白名單投影就沒有人驗。所以：節錄只刮金鑰樣式（readers），
    # 其餘欄位由這裡驗證不含任何絕對路徑或金鑰。
    rendered = _json.dumps(_without_excerpts(results), ensure_ascii=False, default=str)

    checks = {
        "read_only_contract_unchanged": before == after,
        "tables_checked": len(before),
        "source_refs_seen": len(refs),
        "source_refs_unresolved": unresolved,
        "forbidden_output_hits": scan_output(rendered),
        "tools_answered": sorted(results),
        "tools_failed": errors,
    }
    ok = (
        checks["read_only_contract_unchanged"]
        and not unresolved
        and not checks["forbidden_output_hits"]
        and "omni_project_state" in results
    )
    return {"status": "passed" if ok else "failed", "checks": checks}


EXCERPT_KEYS = ("prompt_excerpt", "response_excerpt", "markdown")


def _without_excerpts(node: Any) -> Any:
    """把 opt-in 的內容節錄拿掉，剩下的就是 metadata 面。"""
    if isinstance(node, Mapping):
        return {k: _without_excerpts(v) for k, v in node.items() if k not in EXCERPT_KEYS}
    if isinstance(node, (list, tuple)):
        return [_without_excerpts(item) for item in node]
    return node


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
