"""ADR-008 P5-R3 subprocess dispatcher：白名單 argv、沙盒 cwd、環境清理。

安全邊界（對應 ADR-008 D2 subprocess 條款與 acceptance criteria #2）：

- 只用 ``asyncio.create_subprocess_exec(*argv)``——argv 為 server 端組出的
  list，**永遠沒有 shell**、沒有字串拼接命令。
- 環境變數以 allowlist 重建：只轉發位置類變數（PATH、HOME、TEMP…），
  **不繼承任何 API key / token / secret**；本機 agent CLI（Claude Code、
  Codex）使用自己家目錄下的既有登入憑證，不經過本程序轉交。
- cwd 必須是實際存在的目錄，並以 ``resolve()`` 後的絕對路徑執行，
  拒絕消失或非目錄的路徑；呼叫端（executor template）另負責把 cwd
  限制在已探索的本機 repo roots 內。
- 硬性 timeout：逾時即 ``kill()`` 整個行程並如實回報，不留殭屍。
- 執行中的行程登記於 in-memory registry，供 cancel endpoint 中止；
  這是 P5-R2 in-process 模式做不到、P5-R3 dispatcher 才提供的能力。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

# 位置與地區類變數：讓 CLI 找得到自己的安裝與設定；不含任何 secret。
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
)

OUTPUT_CAPTURE_LIMIT = 200_000  # 單 stream 保留上限；再長的輸出如實標記截斷。

# receipt_id -> Popen-like process；cancel endpoint 用來中止執行中的 job。
_RUNNING: dict[int, Any] = {}


class DispatchTimeout(RuntimeError):
    """行程超過硬性 timeout，已被 kill；payload 保留部分輸出供 receipt。"""

    def __init__(self, payload: dict[str, Any]):
        super().__init__("agent subprocess timed out")
        self.payload = payload


class DispatchRejected(RuntimeError):
    """前置檢查失敗（找不到 CLI、cwd 不合法等）；error_code 穩定。"""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


def build_subprocess_env() -> dict[str, str]:
    """以 allowlist 重建環境；API key 類變數（GEMINI_API_KEY…）一律不轉發。"""
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in ENV_ALLOWLIST and value
    }


def _validate_argv(argv: list[str]) -> list[str]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise DispatchRejected("invalid_argv", "argv 必須是非空字串 list")
    items = [str(item) for item in argv]
    if any(not item for item in items):
        raise DispatchRejected("invalid_argv", "argv 不得含空字串")
    return items


def _validate_cwd(cwd: str | Path) -> Path:
    try:
        resolved = Path(cwd).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DispatchRejected("cwd_not_found", "工作目錄不存在或無法解析") from exc
    if not resolved.is_dir():
        raise DispatchRejected("cwd_not_directory", "工作目錄必須是資料夾")
    return resolved


def _truncate(raw: bytes) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > OUTPUT_CAPTURE_LIMIT:
        return text[:OUTPUT_CAPTURE_LIMIT], True
    return text, False


async def _exec(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    receipt_id: int | None,
) -> dict[str, Any]:
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if receipt_id is not None:
        _RUNNING[receipt_id] = process
    try:
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except (asyncio.TimeoutError, TimeoutError):
            process.kill()
            stdout_raw, stderr_raw = await process.communicate()
            stdout, stdout_truncated = _truncate(stdout_raw or b"")
            raise DispatchTimeout(
                {
                    "exit_code": None,
                    "stdout": stdout,
                    "stdout_truncated": stdout_truncated,
                    "stderr": _truncate(stderr_raw or b"")[0][:2000],
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "timed_out": True,
                }
            )
    finally:
        if receipt_id is not None:
            _RUNNING.pop(receipt_id, None)

    stdout, stdout_truncated = _truncate(stdout_raw or b"")
    stderr, _ = _truncate(stderr_raw or b"")
    return {
        "exit_code": process.returncode,
        "stdout": stdout,
        "stdout_truncated": stdout_truncated,
        "stderr": stderr[:2000],
        "duration_seconds": round(time.monotonic() - started, 3),
        "timed_out": False,
    }


def run_agent_subprocess(
    argv: list[str],
    *,
    cwd: str | Path,
    timeout_seconds: int,
    receipt_id: int | None = None,
) -> dict[str, Any]:
    """同步入口（executor worker thread 內呼叫）；全程 fail-closed。"""
    items = _validate_argv(argv)
    env = build_subprocess_env()
    resolved_binary = shutil.which(items[0], path=env.get("PATH"))
    if resolved_binary is None:
        raise DispatchRejected(
            "cli_not_found",
            f"在 PATH 中找不到 CLI「{items[0]}」；請確認已安裝並可從終端機執行",
        )
    items[0] = resolved_binary
    resolved_cwd = _validate_cwd(cwd)
    timeout_seconds = min(1800, max(3, int(timeout_seconds)))
    return asyncio.run(_exec(items, resolved_cwd, env, timeout_seconds, receipt_id))


def is_running_registered(receipt_id: int) -> bool:
    """cancel 前的登記檢查：只有 dispatcher job 會登記 OS 行程。"""
    return int(receipt_id) in _RUNNING


def kill_running(receipt_id: int) -> bool:
    """中止執行中的 subprocess job；in-process job 沒有登記、回 False。"""
    process = _RUNNING.get(int(receipt_id))
    if process is None:
        return False
    try:
        process.kill()
        return True
    except ProcessLookupError:
        return False
