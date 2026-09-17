"""Codex 的 transcript 格式與探索（ADR-025，TODO D9）。

Codex 在 `~/.codex` 底下同時存在三種東西，這個模組各配一個 parser：

- `history.jsonl`——只有提問，沒有回應（`missing`）。
- `sessions/**/*.json`——舊版 session 快照，`{"session": …, "items": [...]}`。
- `sessions/**/*.jsonl`——rollout 串流，同一輪的最終答案可能出現在 `response_item`、
  `event_msg/agent_message` 或 `event_msg/item_completed` 三種形狀裡。

三者的共同點：使用者的下一個 turn 才能封閉前一輪，`phase == "final_answer"` 是唯一的明確終止標記。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, List

from core.time_utils import get_local_now
from watchers.transcripts.base import (
    TranscriptTurn,
    TurnEvidence,
    build_turn_key,
    classify_response_status,
    extract_text_from_content,
    iter_jsonl_records,
    normalize_assistant_candidate,
    parse_timestamp_safe,
    select_last_assistant_message,
)

PLATFORM = "codex"
HISTORY_FILENAME = "history.jsonl"


def codex_home() -> Path:
    return Path.home() / ".codex"


# ---------------------------------------------------------------------------
# 1. history.jsonl
# ---------------------------------------------------------------------------


def parse_history(history_file: Path) -> Iterator[TranscriptTurn]:
    for line_number, item in iter_jsonl_records(history_file):
        prompt_text = item.get("prompt") or item.get("text") or item.get("display")
        if not prompt_text or len(prompt_text.strip()) < 2:
            continue

        event_time = parse_timestamp_safe(item.get("ts") or item.get("timestamp") or item.get("time"))
        if not event_time:
            continue

        cwd = item.get("cwd") or item.get("project")
        yield TranscriptTurn(
            platform=PLATFORM,
            conv_id=item.get("session_id"),
            prompt=prompt_text.strip(),
            response=None,
            cwd=str(cwd) if cwd else None,
            timestamp=event_time,
            turn_key=build_turn_key(PLATFORM, str(history_file), line_number),
            source_path=str(history_file.resolve()),
            source_position=line_number,
            response_status="missing",
        )


# ---------------------------------------------------------------------------
# 2. sessions/**/*.json（舊版格式）
# ---------------------------------------------------------------------------


def parse_json_session(file_path: Path, *, now=get_local_now) -> Iterator[TranscriptTurn]:
    with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
        data = json.load(fp)
    session_info = data.get("session", {})
    session_id = session_info.get("id")
    session_time = parse_timestamp_safe(session_info.get("timestamp"))
    items = data.get("items", [])

    current_prompt = ""
    # session timestamp 只認第一輪：flush 之後歸零，後面的輪次以掃描時間回填。
    current_time = session_time
    current_position: int | None = None
    assistant_messages: List[str] = []

    def flush_turn(boundary_closed: bool = False) -> Iterator[TranscriptTurn]:
        nonlocal current_prompt, current_time, current_position, assistant_messages
        if not current_prompt:
            return
        response = select_last_assistant_message(assistant_messages) or None
        yield TranscriptTurn(
            platform=PLATFORM,
            conv_id=session_id,
            prompt=current_prompt,
            response=response,
            cwd=None,
            timestamp=current_time or now(),
            turn_key=build_turn_key(PLATFORM, str(file_path), current_position or 0),
            source_path=str(file_path.resolve()),
            source_position=current_position,
            response_status=classify_response_status(response, boundary_closed=boundary_closed),
        )
        current_prompt = ""
        current_time = None
        current_position = None
        assistant_messages = []

    for item_index, it in enumerate(items, start=1):
        role = it.get("role")
        content = extract_text_from_content(it.get("content"))
        if role == "user" and content:
            if "<recommended_plugins>" in content or len(content) < 2:
                continue
            yield from flush_turn(boundary_closed=True)
            current_prompt = content
            current_position = item_index
        elif role == "assistant" and current_prompt:
            candidate = normalize_assistant_candidate(content)
            if candidate:
                assistant_messages.append(candidate)

    yield from flush_turn(boundary_closed=False)


# ---------------------------------------------------------------------------
# 3. sessions/**/*.jsonl（rollout）
# ---------------------------------------------------------------------------


def parse_jsonl_session(file_path: Path, *, now=get_local_now) -> Iterator[TranscriptTurn]:
    """同一 turn 保留最後一個有效 assistant message。"""
    session_id = None
    session_cwd = None
    current_prompt = ""
    current_time = None
    current_time_verified = False
    current_position: int | None = None
    assistant_messages: List[str] = []
    explicit_final_messages: List[str] = []
    explicit_final_time = None
    explicit_final_position: int | None = None

    def flush_turn(boundary_closed: bool = False) -> Iterator[TranscriptTurn]:
        if not current_prompt:
            return
        final_response = select_last_assistant_message(explicit_final_messages)
        response = final_response or select_last_assistant_message(assistant_messages) or None
        yield TranscriptTurn(
            platform=PLATFORM,
            conv_id=session_id,
            prompt=current_prompt,
            response=response,
            cwd=session_cwd,
            timestamp=current_time or now(),
            turn_key=build_turn_key(PLATFORM, str(file_path), current_position or 0),
            source_path=str(file_path.resolve()),
            source_position=current_position,
            response_status=classify_response_status(
                response,
                explicit_final=bool(final_response),
                boundary_closed=boundary_closed,
            ),
            evidence=(
                TurnEvidence(
                    started_at=current_time,
                    start_position=current_position,
                    session_id=session_id,
                    cwd=session_cwd,
                    completed_at=explicit_final_time if final_response else None,
                    end_position=explicit_final_position if final_response else None,
                    completion_evidence_kind="codex_final_answer" if final_response else None,
                )
                if current_time_verified
                else None
            ),
        )

    for line_number, d in iter_jsonl_records(file_path):
        t = d.get("type")
        ts = parse_timestamp_safe(d.get("timestamp"))
        payload = d.get("payload", {})

        if t == "session_meta" and isinstance(payload, dict):
            session_id = payload.get("id")
            session_cwd = payload.get("cwd")

        elif t == "response_item" and isinstance(payload, dict):
            role = payload.get("role")
            content = extract_text_from_content(payload.get("content"))

            if role == "user" and content:
                if "<recommended_plugins>" in content or len(content) < 2:
                    continue
                yield from flush_turn(boundary_closed=True)
                current_prompt = content
                current_time = ts or now()
                current_time_verified = ts is not None
                current_position = line_number
                assistant_messages = []
                explicit_final_messages = []
                explicit_final_time = None
                explicit_final_position = None

            elif role == "assistant" and current_prompt:
                candidate = normalize_assistant_candidate(content)
                if candidate and candidate not in assistant_messages:
                    assistant_messages.append(candidate)
                if payload.get("phase") == "final_answer" and candidate:
                    if candidate not in explicit_final_messages:
                        explicit_final_messages.append(candidate)
                    explicit_final_time = ts
                    explicit_final_position = line_number

        elif t == "event_msg" and isinstance(payload, dict):
            p_type = payload.get("type")
            if p_type == "agent_message" and current_prompt:
                msg_text = extract_text_from_content(payload.get("message") or payload.get("text"))
                candidate = normalize_assistant_candidate(msg_text)
                if candidate and candidate not in assistant_messages:
                    assistant_messages.append(candidate)
            elif p_type == "item_completed" and current_prompt:
                item = payload.get("item", {})
                if isinstance(item, dict) and item.get("type") == "AgentMessage":
                    msg_text = extract_text_from_content(item.get("content"))
                    candidate = normalize_assistant_candidate(msg_text)
                    if candidate and candidate not in assistant_messages:
                        assistant_messages.append(candidate)
                    if item.get("phase") == "final_answer" and candidate:
                        if candidate not in explicit_final_messages:
                            explicit_final_messages.append(candidate)
                        explicit_final_time = ts
                        explicit_final_position = line_number

    yield from flush_turn(boundary_closed=False)


# ---------------------------------------------------------------------------
# 共同介面
# ---------------------------------------------------------------------------


def discover(cfg=None, *, full_history: bool = False, now=get_local_now) -> List[Path]:
    codex_dir = codex_home()
    if not codex_dir.exists():
        return []

    found: List[Path] = []
    history_file = codex_dir / HISTORY_FILENAME
    if history_file.exists():
        found.append(history_file)

    sessions_dir = codex_dir / "sessions"
    if sessions_dir.exists():
        for s_file in sessions_dir.glob("**/*"):
            if not s_file.is_file() or s_file.suffix not in (".json", ".jsonl"):
                continue
            found.append(s_file)
    return found


def parse(path: Path, *, cfg=None, now=get_local_now) -> Iterator[TranscriptTurn]:
    if path.name == HISTORY_FILENAME and path.parent == codex_home():
        yield from parse_history(path)
    elif path.suffix == ".json":
        yield from parse_json_session(path, now=now)
    else:
        yield from parse_jsonl_session(path, now=now)
