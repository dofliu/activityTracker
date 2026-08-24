"""Cross-platform OS integration without shell-string execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def open_web_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("只允許開啟 http/https URL")
    webbrowser.open(url)


def _terminal_command(folder: str) -> list[str]:
    if sys.platform == "win32":
        return ["powershell.exe", "-NoExit", "-WorkingDirectory", folder]
    if sys.platform == "darwin":
        return ["open", "-a", "Terminal", folder]
    for candidate in ("x-terminal-emulator", "gnome-terminal", "konsole"):
        executable = shutil.which(candidate)
        if executable:
            if candidate == "gnome-terminal":
                return [executable, "--working-directory", folder]
            if candidate == "konsole":
                return [executable, "--workdir", folder]
            return [executable, "--working-directory", folder]
    raise RuntimeError("找不到可用的 terminal emulator")


def _open_command(target_path: str) -> list[str]:
    if sys.platform == "win32":
        return ["explorer.exe", target_path]
    if sys.platform == "darwin":
        return ["open", target_path]
    executable = shutil.which("xdg-open")
    if not executable:
        raise RuntimeError("找不到 xdg-open")
    return [executable, target_path]


def build_open_command(path: str, action: str = "explorer") -> list[str]:
    """建立可測試的 argv；不回傳 shell command string。"""
    target = Path(os.path.expandvars(path)).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"本機路徑不存在: {path}")

    normalized_action = (action or "explorer").lower()
    if normalized_action == "vscode":
        executable = shutil.which("code") or "code"
        return [executable, str(target)]
    if normalized_action == "terminal":
        folder = target if target.is_dir() else target.parent
        return _terminal_command(str(folder))
    if normalized_action != "explorer":
        raise ValueError(f"不支援的本機操作: {action}")
    return _open_command(str(target))


def open_local_path(path: str, action: str = "explorer") -> None:
    subprocess.Popen(build_open_command(path, action), shell=False)


def build_clipboard_command(
    system: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[str]:
    """建立 clipboard argv；允許測試注入 OS 與 executable discovery。"""
    current_system = system or sys.platform
    find_executable = which or shutil.which
    if current_system == "win32":
        return ["powershell.exe", "-NoProfile", "-Command", "$input | Set-Clipboard"]
    if current_system == "darwin":
        return ["pbcopy"]
    for candidate, args in (("wl-copy", []), ("xclip", ["-selection", "clipboard"])):
        executable = find_executable(candidate)
        if executable:
            return [executable, *args]
    raise RuntimeError("找不到 wl-copy 或 xclip，無法寫入 Linux clipboard")


def copy_text_to_clipboard(value: str) -> None:
    result = subprocess.run(
        build_clipboard_command(),
        input=value,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "clipboard command failed")
