"""Desktop AI 本機資料來源探索；只回傳路徑存在性，不讀取雲端快取內容。

**位置知識已經搬到 `coding_agent_transcripts.desktop`**（ADR-033 決策二）：
「Claude Desktop 把檔案放在哪」跟「那個 JSONL 怎麼解」是同一類知識，所以跟著 parser 走。
這裡 re-export 那三支，讓既有呼叫端（`core/capture_coverage.py`、
`scripts/verify_release_artifacts.py`、`watchers/transcripts/`）一行都不用改。

留在這裡的兩支是**應用問題**不是格式問題——它們在回答「這台機器上有沒有東西可採」。
"""

from __future__ import annotations

from pathlib import Path

from coding_agent_transcripts.desktop import (
    default_claude_desktop_data_dir,
    default_claude_desktop_logs_dir,
    iter_claude_desktop_project_logs,
)

__all__ = [
    "default_claude_desktop_data_dir",
    "default_claude_desktop_logs_dir",
    "iter_claude_desktop_project_logs",
    "claude_desktop_cloud_cache_detected",
    "has_claude_desktop_project_logs",
]


def claude_desktop_cloud_cache_detected(data_dir: Path | None = None) -> bool:
    """只偵測 cache 是否存在；禁止把 LevelDB 存在誤報成可用 transcript。"""
    root = data_dir or default_claude_desktop_data_dir()
    indexed_db = root / "IndexedDB"
    if not indexed_db.exists():
        return False
    try:
        return any(path.is_dir() for path in indexed_db.glob("https_claude.ai_*.indexeddb.leveldb"))
    except OSError:
        return False


def has_claude_desktop_project_logs(logs_dir: Path | None = None) -> bool:
    root = logs_dir or default_claude_desktop_logs_dir()
    return next(iter(iter_claude_desktop_project_logs(root)), None) is not None
