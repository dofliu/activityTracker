"""每個 coding agent 一個 transcript parser——把四種私有格式讀成同一個 turn 模型。

支援 **Claude Code**、**Claude Desktop**、**Codex**、**Antigravity**。零第三方相依。

    from coding_agent_transcripts import SOURCES

    for source in SOURCES:
        for path in source.discover(cfg):          # cfg 可以是 None
            for turn in source.parse(path):
                print(turn.platform, turn.timestamp, turn.prompt[:40])

`SOURCES` 是使用端唯一需要知道的東西：順序就是掃描順序，`key` 同時是設定鍵
（`watchers.agent_log_watcher.<key>`）與診斷的鍵。要支援新平台，就新增一個模組、
實作 `discover`／`parse`，然後在這裡加一行。

**兩條不會變的線**（ADR-025／ADR-033）：

- `parse` 是產生器而且**不碰資料庫**——它只把一輪對話描述成 `TranscriptTurn`，
  要不要寫、寫去哪，是使用端的事。這讓每個 parser 都能在沒有資料庫、沒有設定檔的情況下單獨測。
- 這個套件**不認識任何應用概念**：沒有 SQL、沒有 checkpoint、沒有排程。
  那些是使用端的責任。

`turn_key` 是 `sha256(platform|resolve 後的路徑|source_position)`，設計上就是拿來當
跨重啟去重鍵的——**它的值是對外契約，不能因為重構而改變**（ADR-033 為此立了一條測試）。
"""

from __future__ import annotations

from coding_agent_transcripts import antigravity, claude_code, claude_desktop, codex
from coding_agent_transcripts.base import (
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
from coding_agent_transcripts.config import (
    CONFIG_KEYS,
    EMPTY_CONFIG,
    DictConfig,
    TranscriptConfig,
)
from coding_agent_transcripts.drift import DRIFT_WINDOW_DAYS, empty_drift, evaluate_drift

SOURCES = (
    TranscriptSource("claude_code", "Claude Code", claude_code.discover, claude_code.parse,
                     claude_code.default_logs_dir),
    TranscriptSource("claude_desktop", "Claude Desktop", claude_desktop.discover, claude_desktop.parse,
                     claude_desktop.default_logs_dir),
    TranscriptSource("codex", "Codex", codex.discover, codex.parse,
                     codex.default_logs_dir),
    TranscriptSource("antigravity", "Antigravity", antigravity.discover, antigravity.parse,
                     antigravity.default_logs_dir),
)

SOURCE_KEYS = tuple(source.key for source in SOURCES)

__version__ = "0.1.0"

__all__ = [
    "SOURCES",
    "CONFIG_KEYS",
    "EMPTY_CONFIG",
    "DictConfig",
    "TranscriptConfig",
    "__version__",
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
