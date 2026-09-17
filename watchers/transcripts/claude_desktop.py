"""Claude Desktop 的 transcript 探索（ADR-025，TODO D9）。

Desktop 寫的是與 Claude Code **同一種 JSONL**，所以這裡只有「檔案在哪」是新的：
Cowork／local-agent 的 transcript 目錄，加上首次啟用時的回看視窗。配對規則直接用
`claude_code.parse_claude_jsonl`——這個 import 就是「兩邊格式相同」這件事的說明；
哪天 Desktop 的格式分家，那一天再把配對邏輯複製過來。

雲端聊天的 LevelDB cache 不在採集範圍內。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List

from core.desktop_sources import (
    default_claude_desktop_logs_dir,
    iter_claude_desktop_project_logs,
)
from core.time_utils import get_local_now
from watchers.transcripts.base import TranscriptTurn
from watchers.transcripts.claude_code import parse_claude_jsonl

PLATFORM = "claude_desktop"
DEFAULT_LOOKBACK_DAYS = 7


def discover(cfg, *, full_history: bool = False, now=get_local_now) -> List[Path]:
    """首次啟用只回補近期資料，避免啟動時一次讀取多年、數 GB 的 session 複本。

    `full_history` 仍提供明確、可稽核的全量回補途徑。
    """
    logs_dir = cfg.get_path(
        "watchers.agent_log_watcher.claude_desktop_logs_path",
        default_claude_desktop_logs_dir(),
    )
    if not logs_dir.exists():
        return []

    if full_history:
        return list(iter_claude_desktop_project_logs(logs_dir))

    lookback_days = max(
        1,
        int(cfg.get("watchers.agent_log_watcher.claude_desktop_initial_lookback_days", DEFAULT_LOOKBACK_DAYS)),
    )
    initial_cutoff = now() - timedelta(days=lookback_days)

    recent: List[Path] = []
    for transcript in iter_claude_desktop_project_logs(logs_dir):
        try:
            if datetime.fromtimestamp(transcript.stat().st_mtime) < initial_cutoff:
                continue
        except OSError:
            continue
        recent.append(transcript)
    return recent


def parse(path: Path, *, cfg=None, now=get_local_now) -> Iterator[TranscriptTurn]:
    yield from parse_claude_jsonl(path, platform=PLATFORM, now=now)
