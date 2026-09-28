"""tool call receipt——**寫檔案，不寫資料庫**（ADR-032 決策五）。

D1（唯讀）與 D6（每次 tool call 留收據）照字面讀是衝突的：留收據就是寫入。解法是把
收據移出資料庫，於是 D1 可以用最強的形式成立：**這個程序沒有一條可寫的資料庫連線**，
而不是「唯讀，但它自己的收據表除外」。凡是需要例外條款的唯讀，證明就會從那個例外開始漏。

這不是為 MCP 破例：`core/acceptance/report.py` 的 `record_human_confirmation()` 就住在
驗收中心套件裡，而同一套件的契約測試斷言「跑完一輪所有資料表列數不變」——
「唯讀模組 ＋ 一份檔案收據」在本專案已經是被接受的形狀。

**落點是 `reports/mcp/` 不是 `logs/mcp/`**：兩者都在 `watchers/file_watcher.DEFAULT_IGNORES`
裡（所以收據不會被自家 watcher 吃回去變成活動事件），但只有 `reports/` 在驗收中心的
閱讀半徑內。**檔名帶 pid**：Claude Code 與 Codex 可以各起一個 MCP 程序，兩個程序併發
append 同一個檔案的原子性本 repo 沒有前例可抄；一個程序一個檔案就不必發明跨程序鎖。

**不輪替**（第一版）：本專案沒有既有的日誌輪替可掛（`main.py` 的 `logging.basicConfig`
沒有任何 `FileHandler`），而為它新增一個沒人實作的設定鍵只會多一個假開關——
`config.example.yaml` 的 `data_lifecycle.backup_retention_days` 就是那種東西。
收據會長大，這件事寫在 ADR 的 Consequences 裡，等真的有人被咬再處理。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 收據只有這六個鍵。**不含 query 原文、不含參數值**（`project` 這種欄位也算參數值）。
# 這不是繼承來的慣例：ADR-013 全文沒有「query 不保存」這條原則（那是 REVIEW 的誤引），
# 所以這是本 ADR 新立的、比現況更嚴的邊界。
RECEIPT_FIELDS = ("ts", "tool", "ok", "result_count", "elapsed_ms", "error_code")


def receipts_dir() -> Path:
    from core.config import get_config
    from core.runtime_paths import resolve_runtime_path

    return resolve_runtime_path(get_config().get("exporters.reports_dir", "reports")) / "mcp"


def receipt_path(now: datetime | None = None) -> Path:
    from core.time_utils import get_local_now

    stamp = (now or get_local_now()).strftime("%Y%m%d")
    return receipts_dir() / f"mcp-receipts-{stamp}-{os.getpid()}.jsonl"


def write_receipt(
    *,
    tool: str,
    ok: bool,
    result_count: int,
    elapsed_ms: int,
    error_code: Optional[str] = None,
    now: datetime | None = None,
) -> Optional[Path]:
    """寫一筆收據。寫不進去**不得**讓 tool call 失敗——收據是觀察用的，不是閘門。"""
    from core.time_utils import get_local_now

    moment = now or get_local_now()
    record: Dict[str, Any] = {
        "ts": moment.isoformat(timespec="seconds"),
        "tool": tool,
        "ok": bool(ok),
        "result_count": int(result_count),
        "elapsed_ms": int(elapsed_ms),
    }
    if error_code:
        record["error_code"] = error_code
    path = receipt_path(moment)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return path


def read_receipts(path: Path) -> list[Dict[str, Any]]:
    """讀回收據；只給 selftest 與契約測試用。"""
    if not path.is_file():
        return []
    out: list[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# selftest 的收據檔名。**與 tool call 收據刻意分開**（不同副檔名、不同前綴）：
# 一個是 append-only 的逐筆流水，一個是一次完整檢查的快照，混在同一個檔案裡
# 會讓「最近一次唯讀證明」變成要自己拼湊的東西。驗收中心讀的是這一個。
SELFTEST_PREFIX = "mcp-selftest-"
SELFTEST_GLOB = f"{SELFTEST_PREFIX}*.json"


def write_selftest_receipt(report: Dict[str, Any], now: datetime | None = None) -> Optional[Path]:
    """把一次 selftest 的結果落成檔案。

    **為什麼要落檔**：A23／A25／A26 是驗收中心的項目，驗收中心只讀本機便宜證據、
    不會替使用者跑 tool（那會變成「驗收自己製造收據」）。selftest 只印到 stdout 的話，
    那三項就永遠只能是 `needs_human`——本來說好機器可查的三項會退化成人工。

    寫不進去不讓 selftest 失敗，理由與 `write_receipt()` 相同。
    """
    from core.time_utils import get_local_now

    moment = now or get_local_now()
    path = receipts_dir() / f"{SELFTEST_PREFIX}{moment.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
    record = {"ts": moment.isoformat(timespec="seconds"), **report}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return None
    return path


def latest_selftest_receipt(directory: Path | None = None) -> Optional[Dict[str, Any]]:
    """最近一次 selftest 的收據；沒有就是 None（**不是**空字典——「沒跑過」要看得出來）。"""
    folder = directory or receipts_dir()
    if not folder.is_dir():
        return None
    files = sorted(folder.glob(SELFTEST_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in files:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["receipt_name"] = candidate.name
            return data
    return None


# selftest 應該答得出來的 tool 名單。**住在這裡而不是 `tools.py`**：驗收中心
# （`core/acceptance/readings.py`）要拿它比對收據，而 `tools.py` 會 import `readers`，
# 那會把 SQLAlchemy 與 `core.models` 拉進驗收的 import 路徑上——驗收讀的是一份 JSON 檔，
# 不該為此多載一整套 ORM。契約測試鎖住它與 `tools.TOOL_NAMES` 逐字相同。
EXPECTED_TOOLS = (
    "omni_project_state",
    "omni_handoff",
    "omni_search_history",
    "omni_open_loops",
    "omni_work_sessions",
    "omni_recent_digest",
    "omni_resolve_ref",
)
