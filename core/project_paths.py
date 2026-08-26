"""設定驅動的專案目錄定位，供 Project State 與 Context Handoff 共用。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from core.config import get_config


class ProjectPathConfig(Protocol):
    """只宣告本模組需要的設定介面，讓測試不依賴全域 Config singleton。"""

    def get_paths(self, key_path: str) -> list[Path]: ...

    def get_path(self, key_path: str, default: str | Path = "") -> Path: ...


def configured_project_search_roots(
    cfg: ProjectPathConfig | None = None,
) -> tuple[Path, ...]:
    """取得可攜的專案搜尋根目錄，並維持設定順序與去重。

    優先使用 `project_resolution.search_roots`。未設定時才借用既有 watcher
    設定，避免把個人電腦的絕對路徑寫回核心程式碼。
    """
    config = cfg or get_config()
    roots = config.get_paths("project_resolution.search_roots")
    if not roots:
        roots = [
            *config.get_paths("watchers.file_watcher.watch_directories"),
            *config.get_paths("watchers.git_watcher.repositories"),
        ]

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        key = str(resolved).casefold()
        if key not in seen:
            unique_roots.append(resolved)
            seen.add(key)
    return tuple(unique_roots)


def find_configured_project_path(
    project_key: str,
    cfg: ProjectPathConfig | None = None,
) -> Path | None:
    """只在使用者明示設定的 roots 下尋找同名專案，不猜測個人路徑。"""
    key = (project_key or "").strip()
    if not key:
        return None

    for root in configured_project_search_roots(cfg):
        candidate = root / key
        if candidate.is_dir():
            return candidate.resolve()
    return None


def configured_self_project_path(
    cfg: ProjectPathConfig | None = None,
) -> Path | None:
    """回傳可選的本專案根目錄；空值時由呼叫端採相對於套件的安全 fallback。"""
    config = cfg or get_config()
    configured = config.get_path("project_resolution.self_project_path")
    if str(configured) in {"", "."}:
        return None
    return configured.expanduser().resolve() if configured.is_dir() else None
