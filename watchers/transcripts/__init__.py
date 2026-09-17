"""每個 AI 平台一個 transcript parser（ADR-025，TODO D9）。

`SOURCES` 是採集服務唯一需要知道的東西：順序就是掃描順序，`key` 同時是設定鍵
（`watchers.agent_log_watcher.<key>`）與 diagnostics 的鍵。要支援新平台，就新增一個
模組、實作 `discover`／`parse`，然後在這裡加一行。
"""

from __future__ import annotations

from watchers.transcripts import antigravity, claude_code, claude_desktop, codex
from watchers.transcripts.base import (
    TranscriptSource,
    TranscriptTurn,
    TurnEvidence,
    build_turn_key,
    classify_response_status,
    clean_prompt_text,
    eof_response_status,
    is_cli_artifact,
    iter_jsonl_records,
    normalize_assistant_candidate,
    parse_timestamp_safe,
    select_last_assistant_message,
)
from watchers.transcripts.drift import DRIFT_WINDOW_DAYS, empty_drift, evaluate_drift

SOURCES = (
    TranscriptSource("claude_code", "Claude Code", claude_code.discover, claude_code.parse),
    TranscriptSource("claude_desktop", "Claude Desktop", claude_desktop.discover, claude_desktop.parse),
    TranscriptSource("codex", "Codex", codex.discover, codex.parse),
    TranscriptSource("antigravity", "Antigravity", antigravity.discover, antigravity.parse),
)

SOURCE_KEYS = tuple(source.key for source in SOURCES)

__all__ = [
    "SOURCES",
    "SOURCE_KEYS",
    "TranscriptSource",
    "TranscriptTurn",
    "TurnEvidence",
    "DRIFT_WINDOW_DAYS",
    "build_turn_key",
    "classify_response_status",
    "clean_prompt_text",
    "empty_drift",
    "eof_response_status",
    "evaluate_drift",
    "is_cli_artifact",
    "iter_jsonl_records",
    "normalize_assistant_candidate",
    "parse_timestamp_safe",
    "select_last_assistant_message",
]
