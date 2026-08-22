import os
import sys
import string
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger("OmniContext.FSUtils")


def get_system_drives() -> List[Dict[str, str]]:
    """列出 Windows 系統中所有可用的硬碟磁碟機代號"""
    drives = []
    if sys.platform == "win32":
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drive_path = f"{letter}:\\"
                    if Path(drive_path).exists():
                        drives.append({
                            "name": f"本機磁碟 ({letter}:)",
                            "path": drive_path
                        })
                bitmask >>= 1
        except Exception:
            # Fallback
            for letter in ["C", "D", "E", "F", "Z"]:
                p = Path(f"{letter}:\\")
                if p.exists():
                    drives.append({"name": f"磁碟 ({letter}:)", "path": str(p)})
    else:
        drives.append({"name": "Root (/)", "path": "/"})
        drives.append({"name": "Home (~)", "path": str(Path.home())})

    return drives


def list_fs_directories(current_path: Optional[str] = None) -> Dict[str, Any]:
    """列出指定目錄下的子資料夾，供前端檔案總管瀏覽"""
    if not current_path or current_path in ["", "/", "root"]:
        drives = get_system_drives()
        home = str(Path.home())
        return {
            "current_path": "",
            "parent_path": None,
            "drives": drives,
            "home_path": home,
            "directories": [{"name": d["name"], "path": d["path"]} for d in drives]
        }

    p = Path(current_path)
    if not p.exists() or not p.is_dir():
        p = Path.home()

    parent = str(p.parent) if p.parent != p else ""
    subdirs = []

    try:
        for entry in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if entry.is_dir():
                if entry.name.startswith(".") or entry.name in ["node_modules", "__pycache__", "$RECYCLE.BIN"]:
                    continue
                subdirs.append({
                    "name": entry.name,
                    "path": str(entry)
                })
    except (PermissionError, OSError) as e:
        logger.warning(f"Permission denied accessing {p}: {e}")

    return {
        "current_path": str(p),
        "parent_path": parent,
        "directories": subdirs
    }


def open_native_folder_picker(initial_dir: Optional[str] = None) -> Optional[str]:
    """彈出 Windows 原生資料夾選擇視窗"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        # 置頂確保視窗不被瀏覽器擋住
        root.attributes("-topmost", True)

        init_path = initial_dir if (initial_dir and Path(initial_dir).exists()) else str(Path.home())
        chosen = filedialog.askdirectory(
            title="OmniContext — 選擇要監控的資料夾",
            initialdir=init_path
        )
        root.destroy()

        if chosen:
            # 轉換正斜線或標準 Windows 反斜線
            return str(Path(chosen).resolve())
        return None
    except Exception as e:
        logger.error(f"Error launching native folder picker: {e}")
        return None
