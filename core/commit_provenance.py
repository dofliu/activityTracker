"""commit 的來源歸因：三種證據分開記帳（TODO F1，ADR-034）。

衍生視圖，不存檔：每次查詢重算，不加欄位、不加表。三個區塊互不合併——
``trailer`` 是 commit 自己的宣告、``temporal_only`` 是時間鄰近（ADR-006）、
``similarity_only`` 是相似度（ADR-006）。只有 ``trailer`` 能讓 ``ai_declared`` 成立。
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from core.context_memory import (
    RELATED_CLAIM_BOUNDARY,
    SESSION_CLAIM_BOUNDARY,
    _project_key,
    collect_work_observations,
    find_related_work,
)
from core.database import get_db
from core.models import AIPromptEvent, GitActivityEvent

LEVEL_TRAILER = "trailer"
LEVEL_TEMPORAL = "temporal_only"
LEVEL_SIMILARITY = "similarity_only"

TRAILER_CLAIM_BOUNDARY = (
    "A trailer is the commit's own declaration. It does not show which lines an AI wrote, "
    "and it does not prove the declaration is true."
)
PROVENANCE_CLAIM_BOUNDARY = (
    "ai_declared reflects allowlisted commit trailers only. Temporal proximity and similarity "
    "are reported separately and never make a commit AI-declared."
)

DEFAULT_WINDOW_MINUTES = 120
MAX_WINDOW_MINUTES = 1440
MAX_TEMPORAL_CANDIDATES = 20

_ANTHROPIC_NOREPLY = "noreply@anthropic.com"
_TRAILER_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*):\s*(.+?)\s*$")
_EMAIL = re.compile(r"<([^<>\s]+@[^<>\s]+)>")


def _split_paragraphs(message: str) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in str(message or "").replace("\r\n", "\n").split("\n"):
        if line.strip():
            current.append(line.rstrip())
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return paragraphs


def parse_trailers(message: str) -> list[tuple[str, str]]:
    """最後一段、且 message 至少兩段時才算 trailer 區（單段就是 subject）。

    該段每一行都得是 ``Key: value``（縮排開頭的續行併進上一個值），否則整段不算。
    """
    paragraphs = _split_paragraphs(message)
    if len(paragraphs) < 2:
        return []
    trailers: list[tuple[str, str]] = []
    for line in paragraphs[-1]:
        if line[:1] in (" ", "\t") and trailers:
            key, value = trailers[-1]
            trailers[-1] = (key, f"{value} {line.strip()}")
            continue
        match = _TRAILER_LINE.match(line)
        if not match:
            return []
        trailers.append((match.group(1), match.group(2)))
    return trailers


def _session_id_from(value: str) -> str:
    return value.strip().rstrip("/").rsplit("/", 1)[-1]


def classify_trailer(key: str, value: str) -> dict[str, Any] | None:
    """ADR-034 D3 白名單；不在表上的 key 回 None（不記錄）。"""
    normalized = key.strip().lower()
    if normalized == "claude-session":
        return {
            "key": key, "value": value, "kind": "session", "ai": True,
            "tool": "claude_code", "session_id": _session_id_from(value),
        }
    if normalized == "co-authored-by":
        email = _EMAIL.search(value)
        address = email.group(1).lower() if email else ""
        if address == _ANTHROPIC_NOREPLY:
            return {"key": key, "value": value, "kind": "co_author", "ai": True, "tool": "claude"}
        return {"key": key, "value": value, "kind": "co_author", "ai": False, "tool": None}
    return None


def trailer_evidence(message: str, *, session: Any) -> dict[str, Any]:
    trailers = [
        item for item in (classify_trailer(k, v) for k, v in parse_trailers(message))
        if item is not None
    ]
    session_links = []
    for item in trailers:
        if item["kind"] != "session":
            continue
        session_id = item["session_id"]
        rows = (
            session.query(AIPromptEvent.id)
            .filter(AIPromptEvent.conversation_id == session_id)
            .order_by(AIPromptEvent.id)
            .all()
        ) if session_id else []
        refs = [f"ai_prompt_events:{row.id}" for row in rows]
        session_links.append({
            "session_id": session_id,
            "resolution": "resolved" if refs else "unresolved",
            "source_refs": refs,
            "reason": None if refs else "本機 ai_prompt_events 沒有 conversation_id 完全相等的列",
        })
    return {
        "level": LEVEL_TRAILER,
        "found": any(item["ai"] for item in trailers),
        "trailers": trailers,
        "sessions": session_links,
        "claim_boundary": TRAILER_CLAIM_BOUNDARY,
    }


def temporal_evidence(commit: dict[str, Any], *, database: Any, window_minutes: int) -> dict[str, Any]:
    until = commit["timestamp"]
    project_key = commit["project_key"]
    if until is None:
        return {
            "level": LEVEL_TEMPORAL, "status": "unavailable", "reason": "commit 沒有時間戳",
            "window_minutes": window_minutes, "project_key": project_key,
            "candidates": [], "claim_boundary": SESSION_CLAIM_BOUNDARY,
        }
    since = until - timedelta(minutes=window_minutes)
    observations = collect_work_observations(
        database=database, since=since, until=until, project=project_key,
    )
    candidates = [
        {
            "source_ref": obs.source_ref,
            "timestamp": obs.timestamp.isoformat(timespec="seconds"),
            "minutes_before_commit": round((until - obs.timestamp).total_seconds() / 60, 1),
            "platform": obs.channel,
            "title": obs.title,
            "trust_status": obs.trust_status,
        }
        for obs in reversed(observations)
        if obs.event_type == "ai_turn" and obs.project_key.casefold() == project_key.casefold()
    ][:MAX_TEMPORAL_CANDIDATES]
    return {
        "level": LEVEL_TEMPORAL,
        "status": "observed" if candidates else "empty",
        "window_minutes": window_minutes,
        "project_key": project_key,
        "candidates": candidates,
        "claim_boundary": SESSION_CLAIM_BOUNDARY,
    }


def _similarity_query(message: str) -> str:
    paragraphs = _split_paragraphs(message)
    if parse_trailers(message):
        paragraphs = paragraphs[:-1]
    return "\n\n".join("\n".join(lines) for lines in paragraphs).strip()


def similarity_evidence(
    message: str,
    *,
    requested: bool,
    database: Any,
    provider: Any | None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "level": LEVEL_SIMILARITY,
        "status": "not_requested",
        "matches": [],
        "claim_boundary": RELATED_CLAIM_BOUNDARY,
    }
    if not requested:
        return block
    query = _similarity_query(message)
    if len(query) < 2:
        block.update(status="unavailable", reason="commit message 去掉 trailer 後沒有可比對的文字")
        return block
    try:
        related = find_related_work(
            query, database=database, cfg=cfg, provider=provider, top_k=20, max_matches=10,
        )
    except Exception as exc:  # Ollama／索引不可用時降級，不 fallback 到雲端（ADR-006）
        block.update(status="unavailable", reason=f"local_semantic_index_unavailable: {type(exc).__name__}")
        return block
    matches = [item for item in related["matches"] if item.get("source_type") == "ai_turn"]
    block.update(
        status="observed" if matches else "no_strong_match",
        threshold=related["threshold"],
        matches=[
            {
                "source_ref": item["source_ref"],
                "score": item["score"],
                "project_key": item.get("project_key"),
                "trust_status": item.get("trust_status"),
            }
            for item in matches
        ],
    )
    return block


def build_commit_provenance(
    event_id: int,
    *,
    database: Any | None = None,
    window_minutes: int | None = None,
    include_similarity: bool = False,
    provider: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any] | None:
    """回傳一個 commit 的三種證據；commit 不存在回 None。"""
    database = database or get_db()
    window = max(1, min(int(window_minutes or DEFAULT_WINDOW_MINUTES), MAX_WINDOW_MINUTES))
    with database.session_scope() as session:
        row = session.get(GitActivityEvent, int(event_id))
        if row is None:
            return None
        message = str(row.message or "")
        commit = {
            "source_ref": f"git_activity_events:{row.id}",
            "repo_name": row.repo_name,
            "commit_hash": row.commit_hash,
            "timestamp": row.timestamp,
            "subject": (_split_paragraphs(message) or [[""]])[0][0],
            "project_key": _project_key(row.repo_name or row.repo_path, "Git Activity"),
        }
        trailer = trailer_evidence(message, session=session)

    temporal = temporal_evidence(commit, database=database, window_minutes=window)
    similarity = similarity_evidence(
        message, requested=include_similarity, database=database, provider=provider, cfg=cfg,
    )
    commit["timestamp"] = commit["timestamp"].isoformat(timespec="seconds") if commit["timestamp"] else None
    return {
        "commit": commit,
        "ai_declared": bool(trailer["found"]),
        "evidence": {
            LEVEL_TRAILER: trailer,
            LEVEL_TEMPORAL: temporal,
            LEVEL_SIMILARITY: similarity,
        },
        "claim_boundary": PROVENANCE_CLAIM_BOUNDARY,
    }
