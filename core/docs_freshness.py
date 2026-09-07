"""文件落後程式（ADR-021）：秘書自己看得出「專案動了很多，文件沒跟上」。

使用者最常手打的一句指令是「檢視目前專案的同步狀態，然後更新說明文件、使用文件與
規劃文件」。前半段秘書早就會做（L0 ``repo_sync_report``）；後半段要改檔，只能走
ADR-008 的 L2——而 L2 缺的不是能力，是**觸發**：沒有任何訊號告訴秘書「現在該提這件事」。

這個模組補上那個訊號，用兩張既有事件表的確定性計數：

- **文件基準**：``file_activity_events`` 裡最近一次被改到的**文件檔**（README／USAGE／
  ROADMAP／STATUS／CHANGELOG／``docs/`` 下的 .md）的時間。
- **之後的程式活動**：``git_activity_events`` 裡該 repo 在那個時間之後的 commit 數與訊息。

commit 數超過門檻、且文件已經幾天沒動，就產生一張 ``docs_behind_code`` 提案；它的動作
就是既有的兩段式 L2（``agent_draft_plan`` 起草 → 你讀過批准 → ``agent_apply_plan`` 實際改檔、
不 commit）。

刻意的邊界：**沒有任何文件異動紀錄的專案不提**。那可能是「這個 repo 沒有文件」，也可能是
「文件目錄不在採集範圍」——兩者機器分不出來，寧可不說（與 ADR-016 A5 同一個判斷）。
不讀 prompt 內容、不呼叫 LLM、不推測文件該寫什麼；訊號只說「數字對不上」。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func

from core.config import get_config
from core.database import get_db
from core.models import FileActivityEvent, GitActivityEvent, SecretaryNote
from core.time_utils import get_local_now

DOCS_CLAIM_BOUNDARY = (
    "「文件落後」只比兩個時間：最近一次文件檔異動，與那之後該 repo 的 commit 數；"
    "不判斷文件內容對不對、不推測該寫什麼。沒有文件異動紀錄的專案一律不提"
    "（分不出「沒有文件」與「沒被採集到」）。"
)

DEFAULT_MIN_COMMITS = 8
DEFAULT_MIN_DAYS = 2
DEFAULT_LOOKBACK_DAYS = 60
MAX_SIGNALS = 3
MAX_DOC_NAMES = 6
MAX_COMMIT_SUBJECTS = 12
_SUBJECT_CHARS = 100
FACTS_MAX_CHARS = 2400

# 文件檔名前綴（不分大小寫）；另外 docs/ 下的 .md 也算。
_DOC_NAME_PREFIXES = (
    "readme", "usage", "roadmap", "status", "changelog", "index",
    "next_session", "adr-", "contributing", "release_checklist", "todo",
)
_DOCS_DIR = re.compile(r"[\\/]docs[\\/]", re.I)


def docs_freshness_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("proactive_secretary.docs_freshness.enabled", True))


def _int_setting(cfg: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


def docs_freshness_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    return {
        "enabled": docs_freshness_enabled(cfg),
        "min_commits": _int_setting(cfg, "proactive_secretary.docs_freshness.min_commits", DEFAULT_MIN_COMMITS, 1, 200),
        "min_days": _int_setting(cfg, "proactive_secretary.docs_freshness.min_days", DEFAULT_MIN_DAYS, 0, 60),
        "lookback_days": _int_setting(cfg, "proactive_secretary.docs_freshness.lookback_days", DEFAULT_LOOKBACK_DAYS, 7, 365),
    }


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def is_doc_file(file_name: str | None, file_path: str | None = None, file_type: str | None = None) -> bool:
    """檔名前綴命中文件清單，或位於 ``docs/`` 底下的 Markdown。"""
    name = str(file_name or "").strip().lower()
    if name.startswith(_DOC_NAME_PREFIXES):
        return True
    suffix = str(file_type or "").lower()
    if not suffix and "." in name:
        suffix = "." + name.rsplit(".", 1)[1]
    return bool(_DOCS_DIR.search(str(file_path or ""))) and suffix in (".md", ".rst")


def doc_baseline(project: str, *, database: Any, since: datetime) -> tuple[datetime, list[str]] | None:
    """該專案最近一次文件異動的時間與檔名；完全沒有紀錄就回 None（不提這個專案）。"""
    with database.session_scope() as session:
        rows = (
            session.query(
                FileActivityEvent.timestamp,
                FileActivityEvent.file_name,
                FileActivityEvent.file_path,
                FileActivityEvent.file_type,
            )
            .filter(FileActivityEvent.project_name == project, FileActivityEvent.timestamp >= since)
            .order_by(FileActivityEvent.timestamp.desc())
            .limit(600)
            .all()
        )
    newest: datetime | None = None
    names: list[str] = []
    for timestamp, file_name, file_path, file_type in rows:
        if not is_doc_file(file_name, file_path, file_type):
            continue
        stamp = _naive(timestamp)
        if newest is None:
            newest = stamp
        if file_name and file_name not in names and len(names) < MAX_DOC_NAMES:
            names.append(str(file_name))
    return (newest, names) if newest is not None else None


def commits_since(project: str, since: datetime, *, database: Any) -> tuple[int, list[str], datetime | None]:
    """(commit 數, 最近幾則 commit 訊息第一行, 最新 commit 時間)。"""
    with database.session_scope() as session:
        total = (
            session.query(func.count(GitActivityEvent.id))
            .filter(GitActivityEvent.repo_name == project, GitActivityEvent.timestamp > since)
            .scalar()
        ) or 0
        rows = (
            session.query(GitActivityEvent.timestamp, GitActivityEvent.message)
            .filter(GitActivityEvent.repo_name == project, GitActivityEvent.timestamp > since)
            .order_by(GitActivityEvent.timestamp.desc())
            .limit(MAX_COMMIT_SUBJECTS)
            .all()
        )
    subjects: list[str] = []
    newest: datetime | None = None
    for timestamp, message in rows:
        if newest is None:
            newest = _naive(timestamp)
        subject = str(message or "").splitlines()[0].strip() if message else ""
        if subject:
            subjects.append(subject[:_SUBJECT_CHARS])
    return int(total), subjects, newest


def _digest_line(project: str, *, database: Any, since: datetime) -> str | None:
    """該專案最近一則工作誌／回顧觀察的正文（已是壓縮過的計數，不含 prompt 原文）。"""
    with database.session_scope() as session:
        row = (
            session.query(SecretaryNote.body)
            .filter(
                SecretaryNote.kind == "observation",
                SecretaryNote.source.in_(("daily_digest", "weekly_review")),
                SecretaryNote.project_key == project,
                SecretaryNote.created_at >= since,
            )
            .order_by(SecretaryNote.created_at.desc())
            .first()
        )
    return str(row[0])[:400] if row and row[0] else None


def compose_docs_facts(
    *,
    project: str,
    baseline: datetime,
    doc_names: list[str],
    commit_count: int,
    subjects: list[str],
    newest_commit: datetime | None,
    digest: str | None,
) -> str:
    """L2 prompt 用的事實區塊；全部由 server 端組出，呼叫端無法注入。"""
    lines = [
        f"專案／repo：{project}",
        f"文件最後一次異動：{baseline:%Y-%m-%d %H:%M}（{'、'.join(doc_names) if doc_names else '檔名未紀錄'}）",
        f"那之後的 commit 數：{commit_count}"
        + (f"，最新一筆 {newest_commit:%Y-%m-%d %H:%M}" if newest_commit else ""),
    ]
    if subjects:
        lines.append("那之後的 commit 訊息（最近 %d 筆）：" % len(subjects))
        lines.extend(f"- {subject}" for subject in subjects)
    if digest:
        lines.append(f"秘書的工作誌摘要：{digest}")
    facts = "\n".join(lines)
    return facts if len(facts) <= FACTS_MAX_CHARS else facts[: FACTS_MAX_CHARS - 1] + "…"


def collect_docs_freshness_signals(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """「文件落後程式」訊號；形狀與其他 triage signal 一致，自動享有 snooze／mute／上限。"""
    cfg = cfg or get_config()
    database = database or get_db()
    now = _naive(now or get_local_now())
    settings = docs_freshness_settings(cfg)
    if not settings["enabled"]:
        return [], {"used": False, "reason": "disabled"}

    since = now - timedelta(days=settings["lookback_days"])
    with database.session_scope() as session:
        repos = [
            str(name)
            for (name,) in session.query(GitActivityEvent.repo_name)
            .filter(GitActivityEvent.repo_name.isnot(None), GitActivityEvent.timestamp >= since)
            .group_by(GitActivityEvent.repo_name)
            .all()
            if name
        ]

    signals: list[dict[str, Any]] = []
    considered: dict[str, Any] = {}
    no_doc_baseline: list[str] = []
    for project in sorted(repos):
        base = doc_baseline(project, database=database, since=since)
        if base is None:
            no_doc_baseline.append(project)
            continue
        baseline, doc_names = base
        commit_count, subjects, newest_commit = commits_since(project, baseline, database=database)
        idle_days = round((now - baseline).total_seconds() / 86400.0, 1)
        considered[project] = {"commits_since_docs": commit_count, "docs_idle_days": idle_days}
        if commit_count < settings["min_commits"] or idle_days < settings["min_days"]:
            continue
        facts = compose_docs_facts(
            project=project, baseline=baseline, doc_names=doc_names, commit_count=commit_count,
            subjects=subjects, newest_commit=newest_commit,
            digest=_digest_line(project, database=database, since=since),
        )
        over = commit_count - settings["min_commits"]
        signals.append({
            "signal_type": "docs_behind_code",
            "project_key": project,
            "subject_ref": f"docs_behind_code:{project}:{baseline:%Y-%m-%d}",
            "evidence_ref": f"file_activity_events:docs:{project}",
            "observed_at": newest_commit or baseline,
            "url": None,
            "age_days": idle_days,
            "open_loop_refs": [],
            "title": f"{project} 的文件落後了：文件最後一次更新後又有 {commit_count} 個 commit",
            "detail": (
                f"文件最後異動 {baseline:%m-%d}（{'、'.join(doc_names) if doc_names else '檔名未紀錄'}）；"
                f"之後 {commit_count} 個 commit、已隔 {idle_days} 天。只比時間與數量，不判斷內容。"
            ),
            "reasons": [f"文件最後更新後有 {commit_count} 個 commit"],
            "score": round(min(0.8, 0.55 + 0.015 * over), 3),
            "docs_facts": facts,
        })

    signals.sort(key=lambda item: -item["score"])
    signals = signals[:MAX_SIGNALS]
    meta = {
        "used": True,
        "min_commits": settings["min_commits"],
        "min_days": settings["min_days"],
        "repos_considered": considered,
        "skipped_no_doc_baseline": no_doc_baseline,
        "signals": len(signals),
        "claim_boundary": DOCS_CLAIM_BOUNDARY,
    }
    return signals, meta
