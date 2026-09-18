"""驗收中心的骨架：狀態字彙、一次探測的結果，與「條件→狀態」的階梯直譯器（ADR-028，TODO D12）。

D12 之前，22 個 `_check_aN` 各自手寫同一套流程：查資料 → 組 evidence → 一連串
`if ... return {"status": ..., "detail": ..., "evidence": evidence}`。流程是一樣的，
所以那 22 份 if/return 是**重複**，不是內容；真正的內容只有兩件事——

1. **去查什麼**（`readings.py` 的 reading 函式）
2. **查到什麼就算什麼**（`items.py` 表格裡的階梯）

這個模組提供把那兩件事接起來的最小機構，外加所有 reading 共用的便宜查詢 helper。

claim boundary 沒有任何放寬（見 ``__init__`` 的模組說明）：這裡只做字典與清單的比對，
不查資料庫、不碰檔案系統以外的東西。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from core.models import AgentExecutionReceipt
from core.runtime_paths import resolve_runtime_path

# ---- 狀態字彙 -------------------------------------------------------------

PASSED = "passed"              # 找到符合判準的收據
PARTIAL = "partial"            # 判準有多項，只有一部分找得到收據
PENDING = "pending"            # 前置齊備但還沒有任何收據
NEEDS_HUMAN = "needs_human"    # 只能由人眼確認；機器最多提供旁證
NOT_CONFIGURED = "not_configured"  # 前置未設定或功能預設關閉——不是失敗
RUNTIME_ONLY = "runtime_only"  # 只有服務執行中的程序看得到（CLI 查不到）
ATTESTED = "attested"          # 機器沒有判準可查，由使用者親眼確認並留下署名收據

# 這幾個狀態代表「還沒拿到收據」，用於彙總與 release gate 判斷。
OUTSTANDING = {PARTIAL, PENDING, NEEDS_HUMAN, RUNTIME_ONLY}
# 完成的兩種形態：機器找到收據，或人親眼確認並署名——兩者永遠分開記帳。
SETTLED = {PASSED, ATTESTED}

COVERAGE_LOOKBACK_DAYS = 8
RECEIPT_SCAN_LIMIT = 500


@dataclass
class Ctx:
    database: Any
    cfg: Any
    now: datetime
    runtime: bool
    session: Any

    @property
    def today(self) -> date:
        return self.now.date()


@dataclass(frozen=True)
class Reading:
    """一次探測的結果。

    ``evidence`` 是**會回到 API 與畫面上**的那份字典——鍵與值都是對外契約。
    ``facts`` 只給階梯用：有些判準或敘述需要的東西刻意不放進 evidence
    （例如 A8 要「最近一筆**成功**的收據」，但 evidence 給的是「最近一筆收據」，
    兩者在最新那筆失敗時並不相同）。把它們分開，才不會為了寫規則而偷偷加欄位。

    reading 函式允許**提早回傳**：功能關掉時就別去做昂貴的收集，回一份只有開關的
    evidence 就好——這與 D12 之前那些「先 return 再算」的分支逐字相同。
    """

    evidence: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)


# 階梯的一列：(判準, 狀態, 敘述)。敘述可以是固定字串，或吃 Reading 的函式。
Rule = tuple[Callable[[Reading], bool], str, "str | Callable[[Reading], str]"]


def _otherwise(_reading: Reading) -> bool:
    return True


#: 階梯的最後一列——走到這裡就是前面都沒中。
OTHERWISE = _otherwise


def has(key: str) -> Callable[[Reading], bool]:
    """evidence[key] 為真（沿用原本的 truthiness：0、空清單、None 都算沒有）。"""
    return lambda reading: bool(reading.evidence.get(key))


def missing(key: str) -> Callable[[Reading], bool]:
    """evidence[key] 為假或不存在。"""
    return lambda reading: not reading.evidence.get(key)


def fact(key: str) -> Callable[[Reading], bool]:
    """facts[key] 為真（只在階梯內部看得到的值）。"""
    return lambda reading: bool(reading.facts.get(key))


def no_fact(key: str) -> Callable[[Reading], bool]:
    return lambda reading: not reading.facts.get(key)


def all_of(*predicates: Callable[[Reading], bool]) -> Callable[[Reading], bool]:
    return lambda reading: all(predicate(reading) for predicate in predicates)


def any_of(*predicates: Callable[[Reading], bool]) -> Callable[[Reading], bool]:
    return lambda reading: any(predicate(reading) for predicate in predicates)


def not_(predicate: Callable[[Reading], bool]) -> Callable[[Reading], bool]:
    return lambda reading: not predicate(reading)


@dataclass(frozen=True)
class Ladder:
    """一項驗收：先 read（去查什麼），再依序走 rules（查到什麼就算什麼）。

    第一條成立的規則說了算——**順序就是語意**，與改寫前的 if/return 順序逐列相同。
    """

    read: Callable[[Ctx], Reading]
    rules: tuple[Rule, ...]

    def __call__(self, ctx: Ctx) -> dict[str, Any]:
        reading = self.read(ctx)
        for predicate, status, detail in self.rules:
            if predicate(reading):
                text = detail(reading) if callable(detail) else detail
                return {"status": status, "detail": text, "evidence": reading.evidence}
        # 每個階梯都以 OTHERWISE 收尾；走到這裡代表表格漏了，寧可大聲壞掉。
        raise AssertionError("階梯沒有任何一列成立——最後一列應該是 OTHERWISE")


# ---- 所有 reading 共用的便宜查詢 -----------------------------------------


def reports_dir(cfg: Any) -> Path:
    return resolve_runtime_path(cfg.get("exporters.reports_dir", "reports"))


def receipts(ctx: Ctx, **filters: Any) -> list[dict[str, Any]]:
    """符合條件的 audit receipt（各種 status 都回，最新在前，只取非敏感欄位）。"""
    query = ctx.session.query(AgentExecutionReceipt)
    for column, value in filters.items():
        attr = getattr(AgentExecutionReceipt, column)
        query = query.filter(attr.in_(value) if isinstance(value, (list, tuple)) else attr == value)
    rows = (
        query.order_by(AgentExecutionReceipt.requested_at.desc(), AgentExecutionReceipt.id.desc())
        .limit(RECEIPT_SCAN_LIMIT)
        .all()
    )
    return [
        {
            "id": row.id,
            "template_id": row.template_id,
            "status": row.status,
            "approved_via": row.approved_via,
            "requested_at": row.requested_at.isoformat(timespec="seconds") if row.requested_at else None,
            "error_code": row.error_code,
        }
        for row in rows
    ]


def succeeded(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["status"] == "succeeded"]


def latest_files(directory: Path, pattern: str, limit: int = 3) -> dict[str, Any]:
    """目錄內符合 pattern 的檔案概況；目錄不存在不是錯誤，只是還沒產生過。"""
    if not directory.is_dir():
        return {"dir": str(directory), "exists": False, "count": 0, "latest": []}
    files = sorted(
        (p for p in directory.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return {
        "dir": str(directory),
        "exists": True,
        "count": len(files),
        "latest": [
            {
                "name": p.name,
                "modified_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
            for p in files[:limit]
        ],
    }


def iso(value: Any) -> str | None:
    """統一的時間字面：None 就是 None，不要變成 "None"。"""
    return value.isoformat(timespec="seconds") if value else None
