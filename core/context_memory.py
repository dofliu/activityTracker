"""P3-4/P3-5：相似歷史提示與 deterministic 工作階段敘事。"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import desc

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, OpenLoop
from core.project_engine import is_bucket_project, normalize_project_name
from core.semantic_index import semantic_search
from core.time_utils import get_local_now


@dataclass(frozen=True)
class WorkObservation:
    """一筆已存在 SQLite 的可追溯活動；不推論實際工時或成果。"""

    timestamp: datetime
    project_key: str
    event_type: str
    source_ref: str
    title: str
    trust_status: str
    channel: str


def _compact_text(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _project_key(value: Any, fallback: str) -> str:
    candidate = normalize_project_name(str(value or "").strip())
    if not candidate or is_bucket_project(candidate):
        candidate = fallback
    return _compact_text(candidate, 255)


def _project_matches(project_key: str, requested: str | None) -> bool:
    if not requested:
        return True
    requested_key = normalize_project_name(requested).casefold()
    current = project_key.casefold()
    return requested_key == current or requested_key in current


def collect_work_observations(
    *,
    database: Any | None = None,
    since: datetime,
    until: datetime,
    project: str | None = None,
    max_events: int = 4000,
) -> list[WorkObservation]:
    """收集已歸戶事件；Window focus 因缺少 canonical project 不混入 session。"""

    if since > until:
        raise ValueError("since must not be later than until")
    database = database or get_db()
    max_events = max(1, min(int(max_events), 20000))
    observations: list[WorkObservation] = []

    with database.session_scope() as session:
        ai_rows = (
            session.query(AIPromptEvent)
            .filter(AIPromptEvent.timestamp >= since, AIPromptEvent.timestamp <= until)
            .order_by(desc(AIPromptEvent.timestamp))
            .limit(max_events)
            .all()
        )
        git_rows = (
            session.query(GitActivityEvent)
            .filter(GitActivityEvent.timestamp >= since, GitActivityEvent.timestamp <= until)
            .order_by(desc(GitActivityEvent.timestamp))
            .limit(max_events)
            .all()
        )
        file_rows = (
            session.query(FileActivityEvent)
            .filter(FileActivityEvent.timestamp >= since, FileActivityEvent.timestamp <= until)
            .order_by(desc(FileActivityEvent.timestamp))
            .limit(max_events)
            .all()
        )

        for row in ai_rows:
            key = _project_key(row.project_tag or row.cwd, "AI Interactions")
            if not _project_matches(key, project):
                continue
            observations.append(WorkObservation(
                timestamp=row.timestamp,
                project_key=key,
                event_type="ai_turn",
                source_ref=f"ai_prompt_events:{row.id}",
                title=f"[{str(row.platform or 'AI').upper()}] {_compact_text(row.prompt_text)}",
                trust_status=str(row.response_status or "legacy_unverified"),
                channel=str(row.platform or "ai"),
            ))

        for row in git_rows:
            key = _project_key(row.repo_name or row.repo_path, "Git Activity")
            if not _project_matches(key, project):
                continue
            observations.append(WorkObservation(
                timestamp=row.timestamp,
                project_key=key,
                event_type="git_commit",
                source_ref=f"git_activity_events:{row.id}",
                title=f"Git: {_compact_text(row.message)}",
                trust_status="git_observed",
                channel="git",
            ))

        for row in file_rows:
            key = _project_key(row.project_name, "File Activity")
            if not _project_matches(key, project):
                continue
            observations.append(WorkObservation(
                timestamp=row.timestamp,
                project_key=key,
                event_type="file_activity",
                source_ref=f"file_activity_events:{row.id}",
                title=f"{str(row.action or 'observed').upper()}: {_compact_text(row.file_name)}",
                trust_status="file_metadata_observed",
                channel=str(row.file_type or "file"),
            ))

    observations.sort(key=lambda item: (item.timestamp, item.source_ref))
    if len(observations) > max_events:
        observations = observations[-max_events:]
    return observations


def _stable_session_id(project_key: str, first: WorkObservation) -> str:
    identity = f"{project_key.casefold()}|{first.timestamp.isoformat()}|{first.source_ref}"
    return hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:20]


def _open_loops_by_project(database: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with database.session_scope() as session:
        rows = (
            session.query(OpenLoop)
            .filter(OpenLoop.status.in_(["open", "stale"]))
            .order_by(desc(OpenLoop.last_seen_at))
            .all()
        )
        for row in rows:
            key = _project_key(row.project_key, "General")
            grouped[key.casefold()].append({
                "id": row.id,
                "status": row.status,
                "title": _compact_text(row.title, 180),
            })
    return grouped


def build_recent_work_sessions(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    hours: int | None = None,
    project: str | None = None,
    gap_minutes: int | None = None,
    limit: int | None = None,
    max_events: int = 4000,
    max_items_per_session: int = 8,
) -> dict[str, Any]:
    """以 project + inactivity gap 分群；session 是時間推論，不是實際任務真相。"""

    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    hours = max(1, min(int(hours or cfg.get("context_memory.recent_hours", 72)), 24 * 90))
    gap_minutes = max(5, min(int(gap_minutes or cfg.get("context_memory.session_gap_minutes", 45)), 24 * 60))
    limit = max(1, min(int(limit or cfg.get("context_memory.max_sessions", 8)), 50))
    max_items_per_session = max(1, min(int(max_items_per_session), 30))
    since = now - timedelta(hours=hours)
    observations = collect_work_observations(
        database=database,
        since=since,
        until=now,
        project=project,
        max_events=max_events,
    )

    groups: list[list[WorkObservation]] = []
    by_project: dict[str, list[WorkObservation]] = defaultdict(list)
    for item in observations:
        by_project[item.project_key].append(item)
    gap = timedelta(minutes=gap_minutes)
    for project_items in by_project.values():
        current: list[WorkObservation] = []
        for item in project_items:
            if current and item.timestamp - current[-1].timestamp > gap:
                groups.append(current)
                current = []
            current.append(item)
        if current:
            groups.append(current)

    open_loops = _open_loops_by_project(database)
    sessions = []
    for items in groups:
        first, last = items[0], items[-1]
        counts = Counter(item.event_type for item in items)
        source_types = sorted(counts)
        span_minutes = max(0.0, (last.timestamp - first.timestamp).total_seconds() / 60)
        count_text = "、".join(
            f"{label} {counts.get(key, 0)}"
            for key, label in (
                ("ai_turn", "AI"),
                ("git_commit", "Git"),
                ("file_activity", "檔案"),
            )
            if counts.get(key, 0)
        )
        narrative = (
            f"{first.project_key} 在 {first.timestamp.strftime('%m/%d %H:%M')}–"
            f"{last.timestamp.strftime('%H:%M')} 觀察到 {len(items)} 筆活動"
            f"（{count_text or '未分類'}）。最近動作：{last.title}"
        )
        sessions.append({
            "session_id": _stable_session_id(first.project_key, first),
            "project_key": first.project_key,
            "started_at": first.timestamp.isoformat(timespec="seconds"),
            "ended_at": last.timestamp.isoformat(timespec="seconds"),
            "span_minutes": round(span_minutes, 1),
            "events_observed": len(items),
            "event_counts": dict(counts),
            "source_types": source_types,
            "headline": last.title,
            "narrative": narrative,
            "open_loops": open_loops.get(first.project_key.casefold(), [])[:3],
            "items": [
                {
                    "timestamp": item.timestamp.isoformat(timespec="seconds"),
                    "source_ref": item.source_ref,
                    "event_type": item.event_type,
                    "title": item.title,
                    "trust_status": item.trust_status,
                    "channel": item.channel,
                }
                for item in items[-max_items_per_session:]
            ],
            "inference_status": "temporal_grouping",
        })

    sessions.sort(key=lambda item: (item["ended_at"], item["session_id"]), reverse=True)
    return {
        "status": "observed" if sessions else "empty",
        "generated_at": now.isoformat(timespec="seconds"),
        "window_hours": hours,
        "gap_minutes": gap_minutes,
        "project": project,
        "observations_considered": len(observations),
        "sessions": sessions[:limit],
        "coverage": {
            "included": ["ai_turn", "git_commit", "file_activity"],
            "excluded": ["window_focus_without_canonical_project"],
        },
        "claim_boundary": (
            "Sessions are deterministic temporal groupings of observed local events. "
            "They do not prove task continuity, productivity, active work duration, or result quality."
        ),
    }


def find_related_work(
    question: str,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    provider: Any | None = None,
    project: str | None = None,
    threshold: float | None = None,
    top_k: int = 8,
    max_matches: int = 5,
) -> dict[str, Any]:
    """使用既有 local semantic index 提示相似歷史，不保存查詢也不判定重複。"""

    cfg = cfg or get_config()
    configured_threshold = cfg.get("context_memory.related_threshold", 0.50)
    threshold = max(0.0, min(float(configured_threshold if threshold is None else threshold), 1.0))
    max_matches = max(1, min(int(max_matches), 10))
    result = semantic_search(
        question,
        database=database,
        cfg=cfg,
        provider=provider,
        project=project,
        top_k=max(max_matches, min(int(top_k), 20)),
    )
    matches = [
        item for item in result["sources"]
        if float(item.get("score", -1.0)) >= threshold
    ][:max_matches]
    projects = sorted({item.get("project_key") for item in matches if item.get("project_key")})
    return {
        "status": "related_history_found" if matches else "no_strong_match",
        "question": str(question).strip(),
        "query_persisted": False,
        "project": project,
        "threshold": threshold,
        "top_score": result["sources"][0]["score"] if result["sources"] else None,
        "projects": projects,
        "matches": matches,
        "advisory": (
            "發現語意相近的本機歷史紀錄；請先檢視來源，不能據此判定工作重複或結論正確。"
            if matches else
            "目前索引中沒有超過門檻的相似紀錄；這不代表歷史中一定沒有相關工作。"
        ),
        "claim_boundary": (
            "Similarity is a local retrieval signal only. It does not validate truth, "
            "completeness, task identity, or whether prior work remains applicable."
        ),
    }
