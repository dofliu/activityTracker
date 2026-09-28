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
