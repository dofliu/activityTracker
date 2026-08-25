"""Desktop AI 本機資料來源探索；只回傳路徑存在性，不讀取雲端快取內容。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable


def default_claude_desktop_data_dir() -> Path:
    """依平台取得 Claude Desktop application data 根目錄。"""
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return roaming / "Claude"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "Claude"


def default_claude_desktop_logs_dir() -> Path:
    return default_claude_desktop_data_dir() / "local-agent-mode-sessions"


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


def iter_claude_desktop_project_logs(logs_dir: Path) -> Iterable[Path]:
    """探索 Claude Desktop 內嵌 Claude Code project transcript，並避免 audit JSONL。"""
    if not logs_dir.exists():
        return

    seen: set[str] = set()
    if sys.platform == "win32":
        # Claude Desktop 的 session 路徑常超過 Windows MAX_PATH；使用 extended path
        # 才能讓 Python 可靠 stat/open，而不會把存在的 transcript 誤判為不存在。
        root_text = str(logs_dir.resolve())
        extended_root = root_text if root_text.startswith("\\\\?\\") else f"\\\\?\\{root_text}"
        for directory, _subdirs, filenames in os.walk(extended_root):
            normalized = directory.lower().replace("/", "\\")
            if "\\.claude\\projects" not in normalized:
                continue
            for filename in filenames:
                if not filename.lower().endswith(".jsonl"):
                    continue
                path = Path(directory) / filename
                key = str(path).lower()
                if key not in seen:
                    seen.add(key)
                    yield path
        return

    # 目前 Windows Desktop 為 workspace/session/local_*/.claude/projects；
    # glob 保留 macOS/Linux 目錄深度相容性。
    patterns = (
        "*/*/local_*/.claude/projects/**/*.jsonl",
        "**/.claude/projects/**/*.jsonl",
    )
    for pattern in patterns:
        try:
            candidates = logs_dir.glob(pattern)
            for path in candidates:
                if not path.is_file():
                    continue
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                yield path
        except OSError:
            continue


def has_claude_desktop_project_logs(logs_dir: Path | None = None) -> bool:
    root = logs_dir or default_claude_desktop_logs_dir()
    return next(iter(iter_claude_desktop_project_logs(root)), None) is not None
