"""唯讀查詢 ＋ 白名單投影。零 SDK 依賴、零寫入。

**為什麼不重用 `core` 的現成查詢函式**（ADR-032 決策三）：它們有三個是寫入函式。
`get_active_projects_list()` 無條件呼叫 `refresh_project_states()`（UPSERT ＋ bulk DELETE）；
`build_daily_digest()` 會寫觀察；而光是拿到 handle——`get_db()` → `Database.__new__` →
`init_db()`——就會跑 migration、寫備份、下 ``PRAGMA journal_mode=WAL``。

所以這裡自己開一條連線。**共用的是 schema 不是查詢**：`core.models` 的 ORM 宣告直接 import
（實測 import 它不會把 `core.database` 拉進 `sys.modules`，也不落任何檔案），欄位名因此
不會跟本體漂移；查詢邏輯必然是第二份，那份漂移風險寫在 ADR-032 的 Consequences，
由 parity 測試守門。

**唯讀是引擎層的事，不是自律**：連線走 ``file:<path>?mode=ro``（在 open 那一層決定，
不可撤銷），每條連線再下一次 ``PRAGMA query_only=ON``（擋寫錯，但同一條連線上關得掉）。
兩個一起用是「安全帶加氣囊」。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from sqlalchemy import create_engine, desc, event, func
from sqlalchemy.orm import sessionmaker

from core.models import (
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
    GitHubRepoState,
    OpenLoop,
    ProjectState,
    SecretaryNote,
)

# `source_ref` 的 table 白名單（ADR-032「共通規定」）。形狀 "<table>:<id>"，沿用
# core/context_memory.py:103/117/131 的既有寫法，不發明新格式。
SOURCE_REF_TABLES = (
    "ai_prompt_events",
    "git_activity_events",
    "file_activity_events",
    "open_loops",
    "project_states",
    "secretary_notes",
    "activity_micro_summaries",
)
_SOURCE_REF_RE = re.compile(r"^(%s):([1-9][0-9]*)$" % "|".join(SOURCE_REF_TABLES))

# 節錄長度：比既有 handoff（prompt 160／response 220）保守一級，且只在
# mcp.metadata_only 為 false 時才出現。
PROMPT_EXCERPT_CHARS = 160
RESPONSE_EXCERPT_CHARS = 220


class ReaderUnavailable(RuntimeError):
    """讀不到資料庫；``code`` 是封閉字串表裡的代碼，不是自由文字（ADR-032 D4）。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ---- 連線 ------------------------------------------------------------------


def database_path() -> Path:
    """資料庫位置。只經過 runtime_paths（它是讀那兩個環境變數的唯一地方）。"""
    from core.config import get_config
    from core.runtime_paths import resolve_runtime_path

    return resolve_runtime_path(get_config().get("database.db_path", "omni_context.db"))


def read_only_uri(path: Path) -> str:
    """唯讀 URI。**一定要用 ``Path.as_uri()``，不可以自己拼 ``f"file:{path.as_posix()}"``。**

    在 Linux 上兩者看起來都對（`file:/tmp/x.db`），但 Windows 的 `as_posix()` 會產生
    `file:D:/a/x.db`——沒有 authority 分隔的兩斜線，`D:` 因此被當成 URI authority，
    SQLite 直接回 ``unable to open database file``（本機以同形狀重現過）。
    `as_uri()` 給的是 `file:///D:/a/x.db`，才是正確的三斜線形式。

    這也是 repo 既有的寫法（`core/data_lifecycle.py:57`、`core/migrations.py:895`、
    `rag/storage.py:164/185`），沿用它就不必再踩一次同一個坑。

    **先 ``resolve()`` 再 ``as_uri()``**：`as_uri()` 對非絕對路徑會丟
    ``ValueError: relative path can't be expressed as a file URI``——那是一個不在
    `tools.ERRORS` 封閉字串表裡的例外，會整個漏到呼叫端（ADR-032 D4 不准這樣）。
    正式路徑一定是絕對的（`resolve_runtime_path()` 永遠回 `.resolve()` 過的），
    但 `read_only_engine(db_path=…)` 允許呼叫端自己傳，傳相對路徑時
    `path.is_file()` 會過、下一行才炸。`core/data_lifecycle.py:62` 也是先 `.resolve()`。
    """
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def read_only_engine(db_path: Path | None = None):
    """唯讀 engine。**不建目錄、不跑 migration、不 create_all。**"""
    path = Path(db_path) if db_path is not None else database_path()
    if not path.is_file():
        raise ReaderUnavailable("database_missing")
    engine = create_engine(
        f"sqlite:///{read_only_uri(path)}&uri=true",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=None,
    )

    @event.listens_for(engine, "connect")
    def _query_only(dbapi_connection, _record):  # pragma: no cover - 由契約測試觸發
        dbapi_connection.execute("PRAGMA query_only=ON")

    return engine


@contextmanager
def read_only_session(db_path: Path | None = None) -> Iterator[Any]:
    """唯讀 session。**永遠不 commit**——`core.database.session_scope()` 結束一定 commit，
    那正是這裡不能用它的理由。"""
    engine = read_only_engine(db_path)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def schema_state(db_path: Path | None = None) -> str:
    """``ok`` / ``schema_unmigrated``。唯讀探針，與 `Database.init_db` 用的是同一支。"""
    from core.migrations import inspect_migration_status

    path = Path(db_path) if db_path is not None else database_path()
    if not path.is_file():
        return "database_missing"
    status = inspect_migration_status(path)
    if status.get("state") == "incompatible" or status.get("pending_versions"):
        return "schema_unmigrated"
    return "ok"


# ---- 唯讀證明用的指紋 --------------------------------------------------------


def database_contract(db_path: Path | None = None) -> Dict[str, Tuple[int, str]]:
    """每張表的 ``(列數, 全表內容雜湊)``。

    **為什麼不是只比列數**：`refresh_project_states()` 的 UPSERT 會讓 `project_states`
    列數一動不動而 `updated_at` 被改寫（ADR-032 Context 陷阱 4 的實測）。只數列數的
    唯讀證明會綠燈放行那次寫入。

    表清單取自 ``sqlite_master`` 而不是 ``Base.metadata``——ORM 之外建的表才看得到。
    """
    import sqlite3

    path = Path(db_path) if db_path is not None else database_path()
    with sqlite3.connect(read_only_uri(path), uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        contract: Dict[str, Tuple[int, str]] = {}
        for table in tables:
            quoted = table.replace('"', '""')
            count = connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
            digest = hashlib.sha256()
            for row in connection.execute(f'SELECT * FROM "{quoted}" ORDER BY rowid'):
                digest.update(repr(row).encode("utf-8", "replace"))
                digest.update(b"\x1e")
            contract[table] = (count, digest.hexdigest())
    return contract


# ---- source_ref ------------------------------------------------------------


def source_ref(table: str, row_id: Any) -> str:
    if table not in SOURCE_REF_TABLES:
        raise ValueError(f"table not in source_ref whitelist: {table}")
    return f"{table}:{int(row_id)}"


def resolve_source_ref(ref: str, db_path: Path | None = None) -> Optional[Dict[str, Any]]:
    """把 ``"<table>:<id>"`` 解回一列——**內部用，不是第七個 tool**。

    ADR-032 記著一件事：全 repo 原本沒有任何函式做得到這件事，所以「每筆結果的
    `source_ref` 回查得到」在 E3 之前只是人讀的約定。這支讓那條收據變成可執行的，
    但**不對外開放**（agent 拿到指標目前仍展不開，`omni_resolve_ref` 排在 E4）。

    另外它只有兩態：查得到／查不到。**裸指標分不出「被刪」與「被重用」**——
    全庫 PK 都是 rowid 別名（沒有 AUTOINCREMENT），刪掉最大的那列之後新插入會重用同一個
    數字。三態 resolver 需要自證指標，那是 E4 的權衡。
    """
    import sqlite3

    match = _SOURCE_REF_RE.match(str(ref or ""))
    if not match:
        return None
    table, row_id = match.group(1), int(match.group(2))
    path = Path(db_path) if db_path is not None else database_path()
    if not path.is_file():
        return None
    with sqlite3.connect(read_only_uri(path), uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            return None
        row = connection.execute(
            f'SELECT * FROM "{table.replace(chr(34), chr(34) * 2)}" WHERE id = ?', (row_id,)
        ).fetchone()
        return dict(row) if row is not None else None


# ---- 投影小工具 --------------------------------------------------------------


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return None


# 只刮金鑰樣式，**不刮路徑**。理由：節錄是使用者自己 opt-in 的內容，路徑常常正是脈絡
# 本身（「我在 /x/y 底下改了什麼」）；而金鑰對呼叫端 agent 零價值、外洩代價卻是實的。
# 這不是安全機制，是把最不值得冒的那個險拿掉——真正的閘門是 `mcp.metadata_only`。
_SECRET_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
)


def scrub_secret_shapes(text: str) -> str:
    for pattern in _SECRET_SHAPES:
        text = pattern.sub("[已遮蔽的金鑰]", text)
    return text


# **內容鍵的唯一定義**（`tools.CONTENT_KEYS` 是它的別名）。內容＝使用者寫的字：
# `mcp.metadata_only: true` 時這些鍵一個都不出現，而就算 false，裡面的金鑰樣式也一律刮掉。
# 定義住在 `readers` 是因為 `tools` import `readers`，反過來會成環。
CONTENT_KEYS: Tuple[str, ...] = (
    "prompt_excerpt", "response_excerpt", "markdown", "excerpt",
    "title", "body", "headline", "narrative",
)


def scrub_content(node: Any) -> Any:
    """把所有內容鍵的值刮過一次金鑰樣式。

    **為什麼需要這一支**：`omni_work_sessions` 接的是 `core` 的函式，它的
    `headline`／`narrative`／`items[].title` 是用 `_compact_text()` 直接切 prompt 前 140 字
    （[core/context_memory.py:108]），**沒有經過 `_excerpt()`**——別的 reader 都有。
    E4 的實機 E2E（真的用官方 SDK client 驅動一輪）就在那裡撈到一個
    `sk-…` 字樣，單元測試抓不到，因為 D4 的掃描面刻意排除內容鍵。

    路徑一樣刻意保留：它常常正是脈絡本身。這裡拿掉的只有「對呼叫端零價值、
    外洩代價卻是實的」那一種。
    """
    if isinstance(node, dict):
        return {
            key: (scrub_secret_shapes(value) if key in CONTENT_KEYS and isinstance(value, str)
                  else scrub_content(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [scrub_content(item) for item in node]
    return node


def _excerpt(text: Any, limit: int) -> Optional[str]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return None
    cleaned = scrub_secret_shapes(cleaned)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _basename(path_value: Any) -> Optional[str]:
    """檔案只送 basename——沿用設定檔裡 Telegram 對話那段的「引用只送檔名」。"""
    raw = str(path_value or "").strip()
    if not raw:
        return None
    return os.path.basename(raw.replace("\\", "/")) or None


def _idle_days(last_activity: Any, now: datetime) -> Optional[int]:
    if not isinstance(last_activity, datetime):
        return None
    return max(0, (now - last_activity).days)


# ---- omni_project_state ------------------------------------------------------


def project_state(
    *,
    project: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """專案狀態。**不呼叫 `refresh_project_states()`**——那是寫入函式。

    所以回的是**主服務上次算出來的狀態**，`state_recorded_at` 就是為了把這件事說出來：
    主服務沒在跑的時候它會舊，讓呼叫端 agent 自己判斷，而不是我們假裝它是此刻的真相。
    """
    from core.time_utils import get_local_now

    now = now or get_local_now()
    limit = max(1, min(int(limit or 20), 100))
    with read_only_session(db_path) as session:
        query = session.query(ProjectState)
        if project:
            query = query.filter(func.lower(ProjectState.project_key) == project.lower())
        if status:
            query = query.filter(ProjectState.status == status)
        rows = query.order_by(desc(ProjectState.last_activity_at)).limit(limit).all()

        projects: List[Dict[str, Any]] = []
        recorded: List[datetime] = []
        for row in rows:
            if isinstance(row.updated_at, datetime):
                recorded.append(row.updated_at)
            open_count = (
                session.query(OpenLoop)
                .filter(OpenLoop.project_key == row.project_key, OpenLoop.status == "open")
                .count()
            )
            repo = (
                session.query(GitHubRepoState)
                .filter(
                    (GitHubRepoState.repo_name == row.display_name)
                    | (GitHubRepoState.repo_name == row.project_key)
                )
                .first()
            )
            has_local = (
                session.query(GitActivityEvent)
                .filter(GitActivityEvent.repo_name == row.project_key)
                .first()
                is not None
            )
            projects.append(
                {
                    "source_ref": source_ref("project_states", row.id),
                    "source_ref_token": ref_token("project_states", row),
                    "project_key": row.project_key,
                    "display_name": row.display_name,
                    "category": row.category,
                    "status": row.status,
                    "last_activity_at": _iso(row.last_activity_at),
                    "idle_days": _idle_days(row.last_activity_at, now),
                    "open_loops_open_count": open_count,
                    # 只送 repo 名字與「有沒有本機路徑」這個布林，不送 local_path（D4）。
                    "repo": {
                        "name": repo.repo_name if repo else row.project_key,
                        "has_local_path": has_local,
                        "github_url": repo.html_url if repo else None,
                    },
                    # github_repo_states 沒有 last_fetch_at 欄位；updated_at 就是
                    # 「我們上次同步這個 repo 狀態的時間」，如實對應過去。
                    "git": {"last_fetch_at": _iso(repo.updated_at) if repo else None},
                }
            )
    return {
        "projects": projects,
        "state_recorded_at": _iso(max(recorded)) if recorded else None,
    }


# ---- omni_handoff ------------------------------------------------------------


def handoff(
    *,
    project: str,
    turns: int = 5,
    include_excerpts: bool = True,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """接續脈絡。**不呼叫 `build_project_handoff()` 也不呼叫 `format_handoff_markdown()`**。

    既有那一對會回 `local_path`、`recent_files[].path`、`recent_ai_turns[].source_path`
    三種本機絕對路徑，並把它們逐字印進 Markdown；而且回傳值裡**一個 row id 都沒有**，
    所以連「就地把 source_path 換成 source_ref」都做不到。這裡重查一次拿 id。
    """
    from core.time_utils import get_local_now

    now = now or get_local_now()
    turns = max(1, min(int(turns or 5), 20))
    with read_only_session(db_path) as session:
        state = (
            session.query(ProjectState)
            .filter(
                (func.lower(ProjectState.project_key) == project.lower())
                | (func.lower(ProjectState.display_name) == project.lower())
            )
            .first()
        )
        project_key = state.project_key if state else project

        loops = (
            session.query(OpenLoop)
            .filter(OpenLoop.project_key == project_key, OpenLoop.status == "open")
            .order_by(desc(OpenLoop.created_at))
            .limit(20)
            .all()
        )
        commits = (
            session.query(GitActivityEvent)
            .filter(GitActivityEvent.repo_name == project_key)
            .order_by(desc(GitActivityEvent.timestamp))
            .limit(5)
            .all()
        )
        files = (
            session.query(FileActivityEvent)
            .filter(FileActivityEvent.project_name == project_key)
            .order_by(desc(FileActivityEvent.timestamp))
            .limit(8)
            .all()
        )
        ai_turns = (
            session.query(AIPromptEvent)
            .filter(AIPromptEvent.project_tag == project_key)
            .order_by(desc(AIPromptEvent.timestamp))
            .limit(turns)
            .all()
        )

        payload: Dict[str, Any] = {
            "project_key": project_key,
            "display_name": state.display_name if state else project_key,
            "status": state.status if state else None,
            "idle_days": _idle_days(state.last_activity_at, now) if state else None,
            "last_activity_at": _iso(state.last_activity_at) if state else None,
            # 未結事項不帶標題（core/secretary/aggregate.py:199 的既有判斷：
            # 標題可能含使用者的原始提問內容）。
            "open_loops": [
                {
                    "source_ref": source_ref("open_loops", loop.id),
                    "source_ref_token": ref_token("open_loops", loop),
                    "project_key": loop.project_key,
                    "status": loop.status,
                    "source_type": loop.source_type,
                    "confidence": loop.confidence,
                    "fingerprint": loop.fingerprint,
                    "created_at": _iso(loop.created_at),
                    "last_seen_at": _iso(loop.last_seen_at),
                }
                for loop in loops
            ],
            "recent_commits": [
                {
                    "source_ref": source_ref("git_activity_events", commit.id),
                    "source_ref_token": ref_token("git_activity_events", commit),
                    "hash": (commit.commit_hash or "")[:8],
                    "message": _excerpt(commit.message, 120),
                    "branch": commit.branch,
                    "changed_at": _iso(commit.timestamp),
                }
                for commit in commits
            ],
            # 檔案只送 basename，不送 file_path。
            "recent_files": [
                {
                    "source_ref": source_ref("file_activity_events", item.id),
                    "source_ref_token": ref_token("file_activity_events", item),
                    "name": item.file_name or _basename(item.file_path),
                    "changed_at": _iso(item.timestamp),
                }
                for item in files
            ],
            "recent_ai_turns": [],
        }

        for turn in ai_turns:
            entry: Dict[str, Any] = {
                "source_ref": source_ref("ai_prompt_events", turn.id),
                "source_ref_token": ref_token("ai_prompt_events", turn),
                "platform": turn.platform,
                "time": _iso(turn.timestamp),
                # turn_key 是 sha256(platform|resolved source_path|source_position)，
                # 單向雜湊、還原不出路徑，所以可以送；source_position 單獨送只是噪音，不送。
                "turn_key": turn.turn_key,
                "response_status": turn.response_status,
            }
            if include_excerpts:
                entry["prompt_excerpt"] = _excerpt(turn.prompt_text, PROMPT_EXCERPT_CHARS)
                entry["response_excerpt"] = _excerpt(
                    turn.response_text, RESPONSE_EXCERPT_CHARS
                )
            payload["recent_ai_turns"].append(entry)

    payload["markdown"] = render_markdown(payload)
    return payload


def render_markdown(payload: Dict[str, Any]) -> str:
    """依**投影後**的欄位重排。刻意不呼叫 `format_handoff_markdown()`——
    那支的工作就是把絕對路徑印給人看，在儀表板上是對的，在 MCP 出口是錯的。"""
    lines: List[str] = [f"# {payload.get('display_name') or payload.get('project_key')}"]
    status = payload.get("status")
    idle = payload.get("idle_days")
    if status or idle is not None:
        lines.append(f"- 狀態：{status or '未知'}（閒置 {idle if idle is not None else '?'} 天）")
    if payload.get("last_activity_at"):
        lines.append(f"- 最後活動：{payload['last_activity_at']}")

    loops = payload.get("open_loops") or []
    lines.append("")
    lines.append(f"## 未結事項（{len(loops)}）")
    if loops:
        for loop in loops:
            lines.append(
                f"- `{loop['source_ref']}` {loop['status']}"
                f"（來源 {loop.get('source_type') or '未知'}，最後出現 {loop.get('last_seen_at') or '—'}）"
            )
        lines.append("")
        lines.append("> 標題刻意不列出：未結事項的標題可能含你貼進 AI 視窗的原文。")
    else:
        lines.append("- 無")

    commits = payload.get("recent_commits") or []
    lines.append("")
    lines.append(f"## 最近 commit（{len(commits)}）")
    lines.extend(
        [f"- `{c['hash']}` {c.get('message') or ''}（{c.get('changed_at') or '—'}）" for c in commits]
        or ["- 無"]
    )

    files = payload.get("recent_files") or []
    lines.append("")
    lines.append(f"## 最近檔案（{len(files)}）")
    lines.extend(
        [f"- {f.get('name') or '—'}（{f.get('changed_at') or '—'}）" for f in files] or ["- 無"]
    )

    turns = payload.get("recent_ai_turns") or []
    lines.append("")
    lines.append(f"## 最近 AI 對話（{len(turns)}）")
    if turns:
        for turn in turns:
            lines.append(f"- [{turn.get('platform')}] {turn.get('time') or '—'} · `{turn['source_ref']}`")
            if turn.get("prompt_excerpt"):
                lines.append(f"  - 問：{turn['prompt_excerpt']}")
            if turn.get("response_excerpt"):
                lines.append(f"  - 答：{turn['response_excerpt']}")
    else:
        lines.append("- 無")

    lines.append("")
    lines.append(
        "> 以上每一筆都帶 `source_ref`（`<table>:<id>`）指回 SQLite row。"
        "相似度與時間鄰近都不構成因果。"
    )
    return "\n".join(lines)


# ---- 唯讀 Database 介面（給 core 的兩支唯讀查詢函式用） --------------------------


class ReadOnlyDatabase:
    """`core` 那兩支**唯讀**查詢函式要的 `database` 介面，只有 `session_scope()`。

    **為什麼這次選重用而不是再抄一份查詢**（與 E3 的 `project_state`／`handoff` 相反）：

    E3 不重用 `get_active_projects_list()`／`build_daily_digest()`，理由是**那些函式會寫**
    （ADR-032 決策三）。這裡要接的兩支不會：`build_recent_work_sessions()` 與
    `semantic_search()` 查證過沒有 `session.add`／`commit`／`record_observation`。
    而且 session 分群有一個 E3 沒有的性質——`_stable_session_id()` 是
    `sha256(project|首筆時間|首筆 source_ref)`。抄第二份就等於保證「同一批資料、
    儀表板與 MCP 給出不同的 session id」，那比欄位漂移更糟：呼叫端 agent 根本沒辦法
    拿 MCP 的結果去對照人在畫面上看到的東西。

    **D1 沒有被繞過。** D1 禁的是 `core.database.get_db()`／`Database()`／
    `session_scope()` 那一套，因為它們會跑 migration、寫備份、下 WAL PRAGMA，而且
    `session_scope()` 結束一定 commit。這個類別三件都不做：engine 是
    `mode=ro` ＋ `PRAGMA query_only=ON`，離開時只 `close()`。真正的保證在引擎層——
    就算 `core` 哪天在那兩支裡加了寫入，拿到的也是 `OperationalError`，
    不是靜悄悄寫進去。契約測試兩邊都守：內容雜湊不變，且驅動時不得出現 commit。
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path

    @contextmanager
    def session_scope(self) -> Iterator[Any]:
        with read_only_session(self._db_path) as session:
            yield session


# ---- source_ref 自證指標（三態 resolver 的前提） --------------------------------

# 每張表的「出生身分」欄位。**只能挑插入之後不會被改寫的欄位**：拿會變的欄位
# （`open_loops.last_seen_at`、`project_states.updated_at`）當身分，合法更新會被誤判成
# 「指標被重用」。契約測試鎖兩件事：七張白名單表一張都不能少，且每個欄位名在真 schema
# 裡存在——schema 改了這裡沒跟上，測試就紅，不會靜悄悄退化成永遠 verified=false。
IDENTITY_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "ai_prompt_events": ("timestamp", "turn_key"),
    "git_activity_events": ("timestamp", "commit_hash"),
    "file_activity_events": ("timestamp", "file_path", "action"),
    "open_loops": ("created_at", "fingerprint"),
    "project_states": ("project_key",),
    "secretary_notes": ("created_at", "kind", "source_ref"),
    "activity_micro_summaries": ("period_start", "period_end"),
}

REF_TOKEN_CHARS = 12
_REF_TOKEN_RE = re.compile(r"^[0-9a-f]{%d}$" % REF_TOKEN_CHARS)


def ref_token(table: str, row: Any) -> Optional[str]:
    """`source_ref` 的自證附件：出生身分欄位的短雜湊。

    **為什麼需要它**：全庫的主鍵都是 rowid 別名（沒有 `AUTOINCREMENT`），刪掉最大的那列
    之後新插入會**重用同一個數字**。所以裸 `<table>:<id>` 在「查得到」時分不出
    「還是原來那一列」與「那個位置已經換人了」。三態 resolver 必須靠呼叫端手上留著
    發出當下的身分，這就是那個東西。

    它是**單向雜湊**，不還原得出內容——所以 `file_path` 可以進雜湊而不會進輸出。
    """
    columns = IDENTITY_COLUMNS.get(table)
    if not columns:
        return None
    digest = hashlib.sha256()
    digest.update(table.encode("utf-8"))
    for column in columns:
        digest.update(b"\x1f")
        value = row[column] if isinstance(row, dict) else getattr(row, column, None)
        digest.update(_token_value(value).encode("utf-8", "replace"))
    return digest.hexdigest()[:REF_TOKEN_CHARS]


def _token_value(value: Any) -> str:
    """把一個欄位值正規化成雜湊輸入。

    **這一步不是潔癖，是正確性。** 同一列會從兩條路進來：ORM 物件（`created_at` 是
    `datetime`）與 `sqlite3.Row`（同一欄是字串 ``'2026-09-28 10:00:00.123456'``）。
    直接 `repr()` 兩邊會得到不同的 token，於是 `omni_resolve_ref` 會把**沒動過的列**
    判成 `stale_reused`——一個只在「發指標的 tool 與解指標的 tool 走不同路」時才出現的
    假警報。所以時間一律先轉成 ISO 字面，兩條路才收斂。
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    text = str(value)
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text


def resolve_ref(
    ref: str,
    token: Optional[str] = None,
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """三態解析：``ok`` / ``stale_gone`` / ``stale_reused``。

    - 沒有那一列 → ``stale_gone``（指標指向的東西不在了）。
    - 有那一列、呼叫端沒給 token → ``ok`` 但 ``verified: false``：**分不出重用**，
      這一點必須讓呼叫端看得到，不能用 `ok` 蓋過去。
    - 有那一列、token 對得上 → ``ok`` ＋ ``verified: true``。
    - 有那一列、token 對不上 → ``stale_reused``：那個 id 現在是別的東西了。

    回的是**投影過**的一列，不是原始 row（`file_path` 這種欄位永遠不出去）。
    """
    match = _SOURCE_REF_RE.match(str(ref or ""))
    if not match:
        return {"status": "invalid_ref", "source_ref": None, "verified": False, "row": None}
    table, row_id = match.group(1), int(match.group(2))
    row = resolve_source_ref(f"{table}:{row_id}", db_path)
    if row is None:
        return {"status": "stale_gone", "source_ref": f"{table}:{row_id}", "verified": False, "row": None}
    actual = ref_token(table, row)
    given = str(token or "").strip().lower()
    if given:
        if not _REF_TOKEN_RE.match(given):
            return {"status": "invalid_ref", "source_ref": f"{table}:{row_id}", "verified": False, "row": None}
        if given != actual:
            # 列還在，但不是當初那一列。**不要回內容**——呼叫端要的是另一個東西。
            return {
                "status": "stale_reused",
                "source_ref": f"{table}:{row_id}",
                "verified": False,
                "row": None,
            }
    return {
        "status": "ok",
        "source_ref": f"{table}:{row_id}",
        "source_ref_token": actual,
        "verified": bool(given),
        "row": row,
    }


# 展開一列時的欄位白名單（第三道同型閘門）。**分兩層**：`metadata` 永遠回，
# `content` 只在 `mcp.metadata_only` 為 false 時回。`file_path`／`prompt_text` 這些
# 一個都不在名單上——展開指標不是繞過投影的後門。
EXPAND_FIELDS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "ai_prompt_events": {
        "metadata": ("platform", "timestamp", "project_tag", "response_status", "turn_key"),
        "content": ("prompt_text", "response_text"),
    },
    "git_activity_events": {
        "metadata": ("timestamp", "repo_name", "branch", "files_changed_count", "insertions", "deletions"),
        "content": ("message",),
    },
    "file_activity_events": {
        "metadata": ("timestamp", "file_name", "file_type", "action", "size_bytes", "project_name"),
        "content": ("diff_summary",),
    },
    "open_loops": {
        "metadata": ("project_key", "status", "source_type", "confidence", "fingerprint",
                     "created_at", "last_seen_at", "resolved_at"),
        "content": ("title", "resolution_note"),
    },
    "project_states": {
        "metadata": ("project_key", "display_name", "category", "status", "last_activity_at", "updated_at"),
        "content": ("last_action_summary",),
    },
    "secretary_notes": {
        "metadata": ("kind", "project_key", "source", "pinned", "created_at"),
        "content": ("title", "body"),
    },
    "activity_micro_summaries": {
        "metadata": ("period_start", "period_end", "provider", "model", "event_count", "created_at"),
        "content": ("summary_text",),
    },
}

_EXPAND_EXCERPT_CHARS = 600


def expand_row(table: str, row: Dict[str, Any], *, include_content: bool) -> Dict[str, Any]:
    """把一列投影成可以送出去的形狀。時間欄位轉字串，內容欄位刮金鑰、限長。"""
    spec = EXPAND_FIELDS.get(table) or {}
    out: Dict[str, Any] = {}
    for key in spec.get("metadata", ()):  # noqa: B007
        if key not in row:
            continue
        value = row[key]
        out[key] = value if not isinstance(value, datetime) else _iso(value)
    if include_content:
        for key in spec.get("content", ()):
            if key in row:
                out[key] = _excerpt(row[key], _EXPAND_EXCERPT_CHARS)
    return out


# ---- omni_open_loops ---------------------------------------------------------

# `core/project_engine.OPEN_LOOP_STATUSES` 的同一組字面。不 import 那個模組不是潔癖：
# `get_open_loops_list()` 無條件呼叫 `get_db()`（[core/project_engine.py:518]），
# 那正是 D1 禁的那一套。兩邊一致由 parity 測試守。
OPEN_LOOP_STATUSES = ("open", "stale", "resolved", "superseded")


def open_loops(
    *,
    project: Optional[str] = None,
    status: str = "open",
    limit: int = 50,
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """未結事項。**不回 `title` 也不回 `resolution_note`**（ADR-032 決策四）。

    預設只回 ``open``——`core/context_memory._open_loops_by_project()` 用的是
    ``{open, stale}``，同一批資料兩套集合會讓呼叫端 agent 困惑，所以 MCP 只認一套，
    要 ``stale`` 必須明講。
    """
    limit = max(1, min(int(limit or 50), 200))
    with read_only_session(db_path) as session:
        query = session.query(OpenLoop).filter(OpenLoop.status == status)
        if project:
            query = query.filter(func.lower(OpenLoop.project_key) == project.lower())
        rows = query.order_by(desc(OpenLoop.created_at)).limit(limit).all()
        loops = [
            {
                "source_ref": source_ref("open_loops", row.id),
                "source_ref_token": ref_token("open_loops", row),
                "project_key": row.project_key,
                "status": row.status,
                "source_type": row.source_type,
                "confidence": row.confidence,
                "fingerprint": row.fingerprint,
                "created_at": _iso(row.created_at),
                "last_seen_at": _iso(row.last_seen_at),
            }
            for row in rows
        ]
    return {"loops": loops, "status_filter": status, "project": project}


# ---- omni_work_sessions ------------------------------------------------------


def work_sessions(
    *,
    project: Optional[str] = None,
    hours: int = 72,
    limit: int = 8,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """工作階段。**接 `core.context_memory.build_recent_work_sessions()`**，不另抄一份。

    投影時處理掉兩件既有輸出裡不該出海的東西：

    - **拿掉附掛的 open loops**（[core/context_memory.py:244]）。那份會帶標題，同時違反
      「未結事項只有一個出口」與「不回標題」。要未結事項就呼叫 `omni_open_loops`。
    - **`items[].title` 是原文**：`ai_turn` 的標題是 `[PLATFORM] <prompt 前 140 字>`
      （[core/context_memory.py:108]），`file_activity` 的是 `ACTION: <檔名>`。所以它跟
      `headline`／`narrative` 一樣算**內容**，受 `mcp.metadata_only` 管，不是 metadata。

    `excluded` 一起送出去——「我沒看哪裡」跟「我看到什麼」一樣是脈絡。
    """
    from core.context_memory import build_recent_work_sessions
    from core.time_utils import get_local_now

    now = now or get_local_now()
    return scrub_content(build_recent_work_sessions(
        database=ReadOnlyDatabase(db_path),
        now=now,
        hours=hours,
        project=project,
        limit=limit,
    ))


# ---- omni_search_history -----------------------------------------------------


class OllamaUnreachable(ReaderUnavailable):
    """Ollama 打不到。**訊息永遠不帶 `str(exc)`**——`core/semantic_index.py:77-80`
    會把回應 body 的前 500 字塞進 `RuntimeError`，那是已查證的洩漏面（ADR-032 D4）。"""

    def __init__(self) -> None:
        super().__init__("ollama_unreachable")


def search_history(
    *,
    query: str,
    project: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 6,
    include_content: bool = True,
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """本機語意檢索。**retrieval-only，不做 LLM 合成**（ADR-032 決策：合成是呼叫端的事）。

    這條路徑**不需要 `[rag]` extra**：向量來自 Ollama `/api/embed`，候選在
    `semantic_documents` 的 float32 BLOB，排序是純 Python cosine——全程沒有
    chromadb／fastembed／rank_bm25／jieba。

    `since` 的語意誠實標示：`semantic_search()` 沒有時間參數，排序是在**全部候選**上做的，
    所以這裡的 `since` 是**排序後過濾**。回傳同時帶 `since_applied: "post_rank"` 與
    `truncated_by_since`，讓呼叫端知道自己拿到的不是「該時段內最相關的 n 筆」。
    """
    # **只為了認得例外型別**，不是為了發請求：打不到 Ollama 時要能把它轉成封閉字串表的
    # `ollama_unreachable`，認不得就只能 `except Exception`，那會連真正的 bug 一起吞掉。
    # 「mcpserver 不得自己發出請求」由契約測試掃**呼叫**而不是掃 import（D5 第二層）。
    import requests as _requests

    from core.semantic_index import semantic_search

    limit = max(1, min(int(limit or 6), 20))
    try:
        raw = semantic_search(
            query,
            database=ReadOnlyDatabase(db_path),
            project=project,
            top_k=limit,
        )
    except ValueError:
        raise ReaderUnavailable("invalid_argument") from None
    except _requests.exceptions.RequestException:
        raise OllamaUnreachable() from None
    except RuntimeError:
        # `OllamaEmbeddingProvider.embed` 對非 2xx 回應丟 RuntimeError，訊息含回應 body。
        raise OllamaUnreachable() from None

    cutoff = _parse_since(since)
    truncated = 0
    sources: List[Dict[str, Any]] = []
    for item in raw.get("sources", []):
        ref = str(item.get("source_ref") or "")
        if not _SOURCE_REF_RE.match(ref):
            # `semantic_documents.source_ref` 欄位寬 1500，不保證是 row 指標；
            # 白名單外的形狀（例如 RAG 報告索引的 `report_file:<相對路徑>`）一律丟掉。
            continue
        updated = str(item.get("source_updated_at") or "")
        if cutoff and updated and updated < cutoff:
            truncated += 1
            continue
        entry: Dict[str, Any] = {
            "citation": item.get("citation"),
            "source_ref": ref,
            "source_type": item.get("source_type"),
            "project_key": item.get("project_key"),
            "trust_status": item.get("trust_status"),
            "score": item.get("score"),
            "source_updated_at": item.get("source_updated_at"),
        }
        if include_content:
            entry["title"] = _excerpt(item.get("title"), 120)
            entry["excerpt"] = _excerpt(item.get("excerpt"), 600)
        sources.append(entry)
    return {
        "sources": sources,
        "embedding_model": raw.get("embedding_model"),
        "indexed_candidates": raw.get("indexed_candidates", 0),
        "since_applied": "post_rank" if since else None,
        "truncated_by_since": truncated,
        # 逐字沿用 core/semantic_index.py:502 的既有立場，不另寫一句。
        "retrieval_claim_boundary": raw.get("claim_boundary"),
    }


def _parse_since(since: Optional[str]) -> Optional[str]:
    """`since` 只收 ISO 日期；回一個可直接做字串比較的 `YYYY-MM-DD`。"""
    raw = str(since or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date().isoformat()
    except ValueError:
        raise ReaderUnavailable("invalid_argument") from None


# ---- omni_recent_digest ------------------------------------------------------

# `record_observation()` 寫進 `secretary_notes.source_ref` 的**邏輯去重鍵**，
# 不是 row 指標（ADR-032「共通規定」特別點名這個混淆）。這裡只拿它當查詢條件，
# 送出去的 `source_ref` 一律是 `secretary_notes:<id>`。
DAILY_DIGEST_PREFIX = "daily_digest:"
WEEKLY_REVIEW_PREFIX = "weekly_review:"


def recent_digest(
    *,
    date: Optional[str] = None,
    weeks_back: Optional[int] = None,
    limit: int = 20,
    include_content: bool = True,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """已寫下的工作誌／週回顧。**不即時產生**。

    `build_daily_digest()` 的內層 `_write()` 會呼叫 `record_observation(...)`
    （[core/activity_digest.py:249-258]）——那是 `secretary_notes` 的 INSERT。
    所以「沒有就順手產一份」在這裡是寫入，不是便利。沒有觀察就說沒有。
    """
    from core.time_utils import get_local_now

    now = now or get_local_now()
    limit = max(1, min(int(limit or 20), 100))

    if weeks_back is not None:
        label, period = _week_label(now, int(weeks_back))
        pattern = f"{WEEKLY_REVIEW_PREFIX}{label}"
        scope: Dict[str, Any] = {"period": label, "period_from": period[0], "period_to": period[1]}
        exact = True
    else:
        day = _parse_since(date) or now.date().isoformat()
        pattern = f"{DAILY_DIGEST_PREFIX}{day}"
        scope = {"date": day}
        exact = False

    with read_only_session(db_path) as session:
        query = session.query(SecretaryNote).filter(SecretaryNote.kind == "observation")
        if exact:
            query = query.filter(SecretaryNote.source_ref == pattern)
        else:
            # `daily_digest:<日期>` 與 `daily_digest:<日期>:<專案>` 兩種都要，
            # 但 `daily_digest:2026-09-1` 不可以撈到 `2026-09-19`——所以是
            # 「等於」或「以 `<pattern>:` 開頭」，不是裸 LIKE。
            query = query.filter(
                (SecretaryNote.source_ref == pattern)
                | (SecretaryNote.source_ref.like(f"{pattern}:%"))
            )
        rows = query.order_by(desc(SecretaryNote.created_at)).limit(limit).all()
        notes: List[Dict[str, Any]] = []
        for row in rows:
            entry: Dict[str, Any] = {
                "source_ref": source_ref("secretary_notes", row.id),
                "source_ref_token": ref_token("secretary_notes", row),
                "project_key": row.project_key,
                "created_at": _iso(row.created_at),
            }
            if include_content:
                entry["title"] = _excerpt(row.title, 160)
                entry["body"] = _excerpt(row.body, 2000)
            notes.append(entry)
    return {"notes": notes, **scope}


def _week_label(now: datetime, weeks_back: int) -> Tuple[str, Tuple[str, str]]:
    """ISO 週標籤，逐字對上 `core/weekly_review.review_period()` 寫進去的那個。

    **`weeks_back=0` 不會被悄悄改成 1。** `review_period()` 自己 `max(1, …)`
    （[core/weekly_review.py:76]），因為週回顧只寫**已結束**的週；把 0 送進去會拿到
    上一週的資料卻標著 0，那是說謊。所以 0 在這裡自己算「進行中的這一週」，
    照實去找（幾乎一定找不到），由 `no_observation` ＋ `next_step` 說明原因。
    """
    from datetime import timedelta

    if weeks_back <= 0:
        start = now.date() - timedelta(days=now.date().weekday())
    else:
        from core.weekly_review import review_period

        start, _end, _label = review_period(now, weeks_back)
    end = start + timedelta(days=6)
    iso_year, iso_week, _ = start.isocalendar()
    return f"{iso_year}-W{iso_week:02d}", (start.isoformat(), end.isoformat())


def ref_tokens_for(refs: Iterable[str], db_path: Path | None = None) -> Dict[str, str]:
    """一批 `source_ref` 的自證附件，**每張表一次查詢**。

    `work_sessions()` 與 `search_history()` 的結果來自 `core` 的函式，手上沒有 ORM 物件，
    所以不能像其他 reader 那樣就地算 token。沒有 token 的指標在
    `omni_resolve_ref` 只能回 `verified: false`——那等於這兩個 tool 的指標比別人弱一級。
    與其把那個不對稱留給呼叫端，不如多做最多七次 `SELECT`（每張白名單表一次）。
    """
    import sqlite3

    wanted: Dict[str, List[int]] = {}
    for ref in refs:
        match = _SOURCE_REF_RE.match(str(ref or ""))
        if match:
            wanted.setdefault(match.group(1), []).append(int(match.group(2)))
    if not wanted:
        return {}
    path = Path(db_path) if db_path is not None else database_path()
    if not path.is_file():
        return {}
    out: Dict[str, str] = {}
    with sqlite3.connect(read_only_uri(path), uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        known = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, ids in wanted.items():
            if table not in known:
                continue
            columns = IDENTITY_COLUMNS.get(table)
            if not columns:
                continue
            quoted = table.replace('"', '""')
            selected = ", ".join('"%s"' % column.replace('"', '""') for column in ("id",) + columns)
            placeholders = ",".join("?" * len(ids))
            rows = connection.execute(
                f'SELECT {selected} FROM "{quoted}" WHERE id IN ({placeholders})', ids
            ).fetchall()
            for row in rows:
                out[f"{table}:{row['id']}"] = ref_token(table, dict(row))
    return out
