"""`discover()` 要的那一點點設定——**兩個方法，不是一個 Config 物件**（ADR-033 決策三）。

四個 `discover` 原本吃的是 OmniContext 的 `Config`，但實際只用到兩個方法與四個鍵：

    cfg.get("watchers.agent_log_watcher.antigravity_logs_path")
    cfg.get_path("watchers.agent_log_watcher.claude_code_logs_path", <預設>)
    cfg.get_path("watchers.agent_log_watcher.claude_desktop_logs_path", <預設>)
    cfg.get("watchers.agent_log_watcher.claude_desktop_initial_lookback_days", 30)

所以介面收斂成一個 protocol。OmniContext 的 `Config` 天然滿足它，使用端一行都不用改；
外部使用者則用 :class:`DictConfig`（或傳 `None`，四個 `discover` 都會退回內建預設）。

**設定鍵名照抄不改。** 那些鍵名帶著 OmniContext 的味道（`watchers.agent_log_watcher.*`），
外部使用者會覺得突兀。改名會讓既有使用者的設定檔失效，而 ADR-033 這一輪的目的是**搬家不是改介面**：
搬家與改介面同時做，出事時分不出是哪一件造成的。鍵名前綴留給下一個大版本。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

# 四個 discover 會讀的鍵，寫死成一張表，好讓外部使用者一眼看完要準備什麼。
CONFIG_KEYS: tuple[str, ...] = (
    "watchers.agent_log_watcher.antigravity_logs_path",
    "watchers.agent_log_watcher.claude_code_logs_path",
    "watchers.agent_log_watcher.claude_desktop_logs_path",
    "watchers.agent_log_watcher.claude_desktop_initial_lookback_days",
)


@runtime_checkable
class TranscriptConfig(Protocol):
    """`discover()` 對設定的全部要求。"""

    def get(self, key_path: str, default: Any = None) -> Any: ...

    def get_path(self, key_path: str, default: str | Path = "") -> Path: ...


class DictConfig:
    """用巢狀或扁平 dict 實作的 :class:`TranscriptConfig`。

    兩種寫法都吃得下，因為外部使用者手上通常是 YAML 讀出來的巢狀 dict，
    而測試裡寫扁平的一行比較省事：

        DictConfig({"watchers": {"agent_log_watcher": {"codex_logs_path": "~/x"}}})
        DictConfig({"watchers.agent_log_watcher.codex_logs_path": "~/x"})
    """

    def __init__(self, data: Mapping[str, Any] | None = None):
        self._data: Mapping[str, Any] = data or {}

    def get(self, key_path: str, default: Any = None) -> Any:
        if key_path in self._data:  # 扁平鍵優先：寫得出來就是想用它
            return self._data[key_path]
        node: Any = self._data
        for part in key_path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def get_path(self, key_path: str, default: str | Path = "") -> Path:
        value = self.get(key_path, default)
        # `expanduser` ＋ `expandvars` 兩個都要，而且順序要與使用端一致
        # （OmniContext `Config.expand_path` 是 `expandvars(expanduser(x))`）。
        # 少了 expanduser，設定檔寫 `~/.claude` 時 `Path("~/.claude").exists()` 永遠是 False
        # ——「設定看起來對、discover 永遠回空清單」這種查不出原因的靜默失敗。
        # 少了 expandvars，同一份設定在套件裡與在 OmniContext 裡行為不同，更糟。
        return Path(os.path.expandvars(os.path.expanduser(str(value or ""))))


EMPTY_CONFIG = DictConfig()
"""沒有設定時用它。四個 `discover` 都吃得下 `None`，這個常數只是讓意圖寫得出來。"""
