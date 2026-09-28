"""``[mcp]`` extra 裝了沒、`mcp.enabled` 開了沒——兩道「能不能啟動」的閘門，單一定義。

形狀完全照抄 `rag/availability.py`（ADR-032 決策二）：只用 ``importlib.util.find_spec``，
不 import 套件本身、不快取，錯誤訊息就是修法。

**設定閘門為什麼也放這裡**：ADR-032 列的六個檔案沒有單獨的 `gate.py`，而
「extra 裝了沒」與「開關開了沒」是同一個問題的兩半——`server` 啟動前要問，
每次 tool call 之前也要再問一次。放在一起才只有一個地方要讀。

**每次 tool call 都要重問**（ADR-032 決策六）：`Manager.reload_config()` 是**程序內**
熱更新，對這個由 client spawn 的子程序無效。所以這裡看設定檔的 mtime，變了就重讀——
「關掉之後還能繼續讀」是不能接受的形狀。成本是每次 tool call 一個 ``stat()``。
"""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List

# import 名稱 → pip 套件名。官方 SDK 只有一個進入點，但保留 dict 形狀與 rag 版一致。
SDK_PACKAGES: Dict[str, str] = {"mcp": "mcp"}
INSTALL_HINT = 'pip install "omnicontext[mcp]"'

# 這個程序只准讀這兩個環境變數（ADR-032 D3）。規則是「不讀」，不是「讀進來再洗乾淨」——
# repo 裡沒有任何就地淨化 os.environ 的現成函式（build_subprocess_env() 只回新 dict）。
ALLOWED_ENV_VARS = ("OMNICONTEXT_HOME", "OMNICONTEXT_CONFIG")


class McpExtraNotInstalled(RuntimeError):
    """MCP server 需要的選用依賴沒裝；訊息就是修法。"""

    def __init__(self, missing: List[str]):
        self.missing = list(missing)
        super().__init__(
            f"MCP Context Server 需要的選用依賴未安裝：{', '.join(self.missing)}。"
            f"請執行 {INSTALL_HINT} 後重試。"
        )


def missing_sdk_packages() -> List[str]:
    """官方 SDK 缺席的 pip 套件名；空清單＝裝好了。"""
    missing: List[str] = []
    for module_name, pip_name in SDK_PACKAGES.items():
        try:
            present = find_spec(module_name) is not None
        except (ImportError, ValueError):  # 壞掉的 namespace／半安裝：當作沒有
            present = False
        if not present:
            missing.append(pip_name)
    return missing


def mcp_extra_installed() -> bool:
    return not missing_sdk_packages()


# ---- 設定閘門 --------------------------------------------------------------

_CONFIG_MTIME: float | None = None


def _config_path() -> Path:
    # core.runtime_paths 是讀 OMNICONTEXT_HOME／OMNICONTEXT_CONFIG 的唯一地方；
    # 這裡借用它，不自己再讀一次 os.environ。
    from core.runtime_paths import default_config_path

    return default_config_path()


def _config(refresh: bool = True) -> Any:
    """取得設定；設定檔 mtime 變了就重讀（跨程序熱更新見模組 docstring）。"""
    from core.config import get_config

    global _CONFIG_MTIME
    cfg = get_config()
    if not refresh:
        return cfg
    try:
        mtime = _config_path().stat().st_mtime
    except OSError:
        return cfg  # 設定檔不存在：沿用手上這份，預設值本來就 fail-closed
    if _CONFIG_MTIME is None:
        _CONFIG_MTIME = mtime
    elif mtime != _CONFIG_MTIME:
        cfg.load()
        _CONFIG_MTIME = mtime
    return cfg


def mcp_enabled(cfg: Any | None = None, *, refresh: bool = True) -> bool:
    """`mcp.enabled`。fail-closed 寫在 ``get`` 的 default 參數裡（危險能力的既有慣例）。"""
    cfg = cfg or _config(refresh=refresh)
    return bool(cfg.get("mcp.enabled", False))


def metadata_only(cfg: Any | None = None, *, refresh: bool = False) -> bool:
    """`mcp.metadata_only`：true＝只回 metadata，不回任何內容節錄。"""
    cfg = cfg or _config(refresh=refresh)
    return bool(cfg.get("mcp.metadata_only", False))


def home_env_snapshot() -> Dict[str, str]:
    """本程序讀到的兩個環境變數；只供 selftest 與契約測試檢查用，不進 tool 輸出。"""
    return {name: os.environ.get(name, "") for name in ALLOWED_ENV_VARS}
