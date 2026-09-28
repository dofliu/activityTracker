"""Claude Desktop 把逐字稿放在哪裡——**位置知識，不是應用知識**。

ADR-033 決策二：這幾支原本住在使用端的 `core/desktop_sources.py`，但它們講的全是
「Claude Desktop 的目錄長什麼樣」，那跟「這個 JSONL 怎麼解」是同一類知識，
所以跟著 parser 走。使用端反過來 import 這裡（`core` 是使用端，使用端相依套件是對的方向）。

**留在使用端沒有搬過來的兩支**：`claude_desktop_cloud_cache_detected()` 與
`has_claude_desktop_project_logs()`。那兩支在回答「這台機器上有沒有東西可採」，
是應用問題不是格式問題。

（ADR-033 原文說搬兩支；實際搬三支——`default_claude_desktop_data_dir()` 是另外兩支的
基礎，把它留在使用端會讓套件反過來相依 `core`，方向就錯了。這是對 ADR 的一次收斂，
理由寫在這裡而不是靜悄悄多搬一個。）
"""

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
