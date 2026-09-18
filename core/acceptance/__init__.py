"""驗收中心（Acceptance Center）：把 docs/TODO.md A 段的「完成判準」機器化。

本專案不把「contract tests 通過」當成「實機可用」，因此 TODO A 段列了一批
只能在使用者自己機器上取得的收據。這個套件做的事只有一件：**去本機找那些
收據到底在不在**，把「做過沒」從記憶與人工翻頁變成可重跑的查詢。

四個模組，各自回答一個問題（ADR-028）：

- ``rules``     —— 用什麼語彙說話（狀態常數、:class:`Reading`、階梯直譯器、共用查詢）
- ``readings``  —— 每一項**去查什麼**
- ``items``     —— 每一項的規格，與**查到什麼就算什麼**的階梯
- ``report``    —— 把清單跑成一份報告（人工署名、release gate、對外入口）

claim boundary（很重要，這是本套件唯一會被誤讀的地方）:

- 只讀。不執行任何驗收動作、不代替使用者操作、不寫任何資料表。
- 只查便宜的本機證據：SQLite 查詢、設定值、檔案是否存在。**不跑 git、
  不連網、不呼叫 LLM、不載入索引**——驗收中心自己不該變成一個負擔。
- ``passed`` 只代表「找到符合該項判準的本機收據」，不代表功能在所有情境
  下正確，也不代表覆蓋率。判準需要人眼比對的項目（例如「卡上每個數字都
  對得上」）永遠停在 ``needs_human``，不會因為查得到旁證就自動變綠。
- 程序內記憶體狀態（檢索 worker 的預熱狀態）只有在**服務執行中的那個程序**
  裡才看得到；CLI 另開程序查不到，一律回 ``runtime_only`` 而不是 ``pending``
  ——查不到不等於沒發生。
"""

from __future__ import annotations

from .items import ITEM_IDS, ITEMS
from .report import (
    ACCEPTANCE_CLAIM_BOUNDARY,
    CONFIRMATIONS_FILENAME,
    build_acceptance_report,
    confirmations_path,
    load_confirmations,
    record_human_confirmation,
)
from .rules import (
    ATTESTED,
    COVERAGE_LOOKBACK_DAYS,
    NEEDS_HUMAN,
    NOT_CONFIGURED,
    PARTIAL,
    PASSED,
    PENDING,
    RECEIPT_SCAN_LIMIT,
    RUNTIME_ONLY,
    Ctx,
    Ladder,
    Reading,
)

__all__ = [
    "ACCEPTANCE_CLAIM_BOUNDARY",
    "ATTESTED",
    "CONFIRMATIONS_FILENAME",
    "COVERAGE_LOOKBACK_DAYS",
    "Ctx",
    "ITEMS",
    "ITEM_IDS",
    "Ladder",
    "NEEDS_HUMAN",
    "NOT_CONFIGURED",
    "PARTIAL",
    "PASSED",
    "PENDING",
    "RECEIPT_SCAN_LIMIT",
    "RUNTIME_ONLY",
    "Reading",
    "build_acceptance_report",
    "confirmations_path",
    "load_confirmations",
    "record_human_confirmation",
]
