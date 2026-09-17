"""Claude Code 的 transcript 格式與探索（ADR-025，TODO D9）。

這個模組是 **Claude JSONL 格式的定義處**：`projects/**/*.jsonl` 的 user／assistant 配對規則
住在這裡，Claude Desktop 寫的是同一種格式，所以 `claude_desktop.py` 明著 import 這裡的
:func:`parse_claude_jsonl`——它只負責「檔案在哪」。

`history.jsonl` 是**沒有 projects 目錄時**的備援：它只有提問、沒有回應，所以每一輪都是
`response_status="missing"`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, List

from core.time_utils import get_local_now
from watchers.transcripts.base import (
    TranscriptTurn,
    TurnEvidence,
    build_turn_key,
    classify_response_status,
    iter_jsonl_records,
    parse_timestamp_safe,
    select_last_assistant_message,
)

PLATFORM = "claude_code"
HISTORY_FILENAME = "history.jsonl"


# ---------------------------------------------------------------------------
# Claude 專用的取文規則
# ---------------------------------------------------------------------------


def extract_claude_user_text(content: Any) -> str:
    """提取 Claude Code 的 User Prompt 文字，並過濾純 tool_result 雜訊"""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "tool_result":
                    continue
                txt = item.get("text") or item.get("input_text") or ""
                if txt:
                    texts.append(txt.strip())
            elif isinstance(item, str):
                texts.append(item.strip())
        return "\n".join(texts).strip()
    if isinstance(content, dict):
        if content.get("type") == "tool_result":
            return ""
        return str(content.get("text") or "").strip()
    return ""


def extract_claude_assistant_text(content: Any) -> str:
    """提取 Claude Code 的 Assistant 文字回覆 (包含 block type == 'text')"""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    txt = item.get("text") or ""
                    if txt:
                        texts.append(txt.strip())
            elif isinstance(item, str):
                texts.append(item.strip())
        return "\n".join(texts).strip()
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "").strip()
        return str(content.get("text") or "").strip()
    return ""


# ---------------------------------------------------------------------------
# 格式：projects/**/*.jsonl（Claude Code 與 Claude Desktop 共用）
# ---------------------------------------------------------------------------


def parse_claude_jsonl(project_log: Path, *, platform: str, now=get_local_now) -> Iterator[TranscriptTurn]:
    """將 Claude JSONL 依 user boundary 配對成輪次。

    `now` 是缺時間戳時用來回填的**函式**（預設本地時間）——回填過的輪次不會附
    :class:`TurnEvidence`，因為背景時間必須同時有來源中的 start 與 final timestamp。
    """
    current_user_prompt = ""
    current_user_time = None
    current_user_time_verified = False
    current_cwd = None
    current_session_id = None
    current_user_position = None
    accumulated_responses: List[str] = []
    explicit_final_responses: List[str] = []
    explicit_final_time = None
    explicit_final_position = None

    def flush_turn(*, boundary_closed: bool) -> Iterator[TranscriptTurn]:
        if not current_user_prompt or not current_user_time:
            return
        explicit_final = select_last_assistant_message(explicit_final_responses)
        full_response = explicit_final or select_last_assistant_message(accumulated_responses) or None
        yield TranscriptTurn(
            platform=platform,
            conv_id=current_session_id,
            prompt=current_user_prompt,
            response=full_response,
            cwd=current_cwd,
            timestamp=current_user_time,
            turn_key=build_turn_key(platform, str(project_log), current_user_position or 0),
            source_path=str(project_log.resolve()),
            source_position=current_user_position,
            response_status=classify_response_status(
                full_response,
                explicit_final=bool(explicit_final),
                boundary_closed=boundary_closed,
            ),
            # 背景時間必須同時有來源中的 start 與 final timestamp；不能用掃描時間補值。
            evidence=(
                TurnEvidence(
                    started_at=current_user_time,
                    start_position=current_user_position,
                    session_id=current_session_id,
                    cwd=current_cwd,
                    completed_at=explicit_final_time if explicit_final else None,
                    end_position=explicit_final_position if explicit_final else None,
                    completion_evidence_kind="claude_end_turn" if explicit_final else None,
                )
                if current_user_time_verified
                else None
            ),
        )

    for line_number, item in iter_jsonl_records(project_log):
        msg_type = item.get("type")
        timestamp = parse_timestamp_safe(item.get("timestamp") or item.get("createdAt"))
        message = item.get("message", {})
        content = message.get("content") if isinstance(message, dict) else item.get("content")

        if msg_type == "user":
            user_text = extract_claude_user_text(content)
            if user_text and len(user_text) >= 2:
                yield from flush_turn(boundary_closed=True)
                current_user_prompt = user_text
                current_user_time = timestamp or now()
                current_user_time_verified = timestamp is not None
                current_cwd = item.get("cwd") or str(project_log.parent)
                current_session_id = item.get("sessionId")
                current_user_position = line_number
                accumulated_responses = []
                explicit_final_responses = []
                explicit_final_time = None
                explicit_final_position = None
        elif msg_type == "assistant":
            assistant_text = extract_claude_assistant_text(content)
            if assistant_text and not assistant_text.startswith("["):
                accumulated_responses.append(assistant_text)
                if isinstance(message, dict) and message.get("stop_reason") == "end_turn":
                    explicit_final_responses.append(assistant_text)
                    explicit_final_time = timestamp
                    explicit_final_position = line_number

    yield from flush_turn(boundary_closed=False)


def parse_claude_history(history_file: Path, *, platform: str = PLATFORM) -> Iterator[TranscriptTurn]:
    """`history.jsonl` 只記提問，不記回應——每一輪都是 `missing`。"""
    for line_number, item in iter_jsonl_records(history_file):
        prompt_text = item.get("display") or item.get("text") or item.get("prompt")
        if not prompt_text or len(prompt_text.strip()) < 2:
            continue

        event_time = parse_timestamp_safe(item.get("timestamp"))
        if not event_time:
            continue

        project_path = item.get("project") or item.get("cwd")
        clean_prompt = prompt_text.strip()
        yield TranscriptTurn(
            platform=platform,
            conv_id=None,
            prompt=clean_prompt,
            response=None,
            cwd=str(project_path) if project_path else None,
            timestamp=event_time,
            turn_key=build_turn_key(platform, str(history_file), line_number),
            source_path=str(history_file.resolve()),
            source_position=line_number,
            response_status="missing",
            dedupe_key=f"claude_code_hist:{clean_prompt[:50]}:{event_time.strftime('%Y%m%d%H%M')}",
        )


# ---------------------------------------------------------------------------
# 共同介面
# ---------------------------------------------------------------------------


def discover(cfg, *, full_history: bool = False, now=None) -> List[Path]:
    """優先 `projects/**/*.jsonl`；**只在完全沒有 projects 日誌時**才回退 `history.jsonl`。"""
    claude_dir = cfg.get_path(
        "watchers.agent_log_watcher.claude_code_logs_path",
        Path.home() / ".claude",
    )
    if not claude_dir.exists():
        return []

    projects_dir = claude_dir / "projects"
    project_files = list(projects_dir.glob("**/*.jsonl")) if projects_dir.exists() else []
    if project_files:
        return project_files

    history_file = claude_dir / HISTORY_FILENAME
    return [history_file] if history_file.exists() else []


def parse(path: Path, *, cfg=None, now=get_local_now) -> Iterator[TranscriptTurn]:
    if path.name == HISTORY_FILENAME:
        yield from parse_claude_history(path)
        return
    yield from parse_claude_jsonl(path, platform=PLATFORM, now=now)
