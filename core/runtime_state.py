"""程序內可變狀態的唯一住處（ADR-027，TODO D11）。

D11 之前，這些狀態散在五個模組的模組層變數裡，而且每個模組各自帶一個「測試用重設鉤子」
——`_reset_state_for_tests()`（兩個）、`_reset_llm_cache_for_tests()`、
`_reset_pending_confirms()`、`reset_advisor_cache()`。那些函式是**產品程式碼**：進 wheel、
誰都能呼叫，而且只清得掉自己記得要清的欄位，漏一個的症狀是「單獨跑會過、一起跑會壞」。

現在每一種狀態是一個小類別，**自己帶自己的鎖**（鎖跟著它保護的資料走），由
:func:`runtime_state` 提供行程預設實例、:func:`new_runtime_state` 給測試一份全新的。

**安全性質一個都沒放寬**（ADR-008／ADR-014）：

- 只存在記憶體——不落庫、不寫檔、不進 log、不跨程序共享；重啟歸零是刻意的，不是缺陷。
- 一次性碼只存**雜湊**與到期時間，never the code itself。

行程預設仍然是一個 process-wide 單例——一個服務程序的 arm 視窗本來就只有一個。D11 買到的
是「可注入」而不是「無全域」：它有名字、能被替換、而且測試不必再去動線上那一份。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Telegram callback 去重表的上限（超過就丟最舊的）
PROCESSED_CALLBACK_CAP = 300


class ConfirmStore:
    """L2 一次性確認碼（ADR-008）：只存雜湊與到期時間。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}

    def issue(self, proposal_id: str, *, code_hash: str, expires_at: datetime, template_id: str) -> None:
        with self._lock:
            self._pending[str(proposal_id)] = {
                "code_hash": code_hash,
                "expires_at": expires_at,
                "template_id": template_id,
            }

    def peek(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pending = self._pending.get(str(proposal_id))
            return dict(pending) if pending else None

    def discard(self, proposal_id: str) -> None:
        with self._lock:
            self._pending.pop(str(proposal_id), None)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def __contains__(self, proposal_id: object) -> bool:
        with self._lock:
            return str(proposal_id) in self._pending

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)


class ApprovalState:
    """Telegram 批准通道的程序內狀態（ADR-014）。

    arm 視窗、一次性 arm code（只存雜湊）、callback 去重表，以及 poller 的觀測值。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.armed_until: Optional[datetime] = None
        self.pending_arm_code: Optional[Dict[str, Any]] = None
        self.poller_running: bool = False
        self.last_poll_at: Optional[datetime] = None
        self.ignored_foreign_updates: int = 0
        self._processed_callbacks: "OrderedDict[str, bool]" = OrderedDict()

    # ---- arm 視窗 ----

    def arm(self, until: datetime) -> datetime:
        with self._lock:
            self.armed_until = until
            return self.armed_until

    def disarm(self) -> None:
        with self._lock:
            self.armed_until = None
            self.pending_arm_code = None

    def is_armed(self, now: datetime) -> bool:
        with self._lock:
            return self.armed_until is not None and now < self.armed_until

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "armed_until": self.armed_until,
                "pending_arm_code": dict(self.pending_arm_code) if self.pending_arm_code else None,
                "poller_running": self.poller_running,
                "last_poll_at": self.last_poll_at,
                "ignored_foreign_updates": self.ignored_foreign_updates,
            }

    # ---- 一次性 arm code ----

    def put_arm_code(self, *, code_hash: str, expires_at: datetime) -> None:
        with self._lock:
            self.pending_arm_code = {"code_hash": code_hash, "expires_at": expires_at}

    def take_arm_code(self) -> Optional[Dict[str, Any]]:
        """取出並銷毀——用過即失效，不管驗證成不成功。"""
        with self._lock:
            pending, self.pending_arm_code = self.pending_arm_code, None
            return pending

    def peek_arm_code(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self.pending_arm_code) if self.pending_arm_code else None

    # ---- callback 去重 ----

    def seen_callback(self, callback_id: str, *, cap: int = PROCESSED_CALLBACK_CAP) -> bool:
        """回 True 代表這個 callback 處理過（重放）；否則記下來並回 False。"""
        with self._lock:
            if callback_id in self._processed_callbacks:
                return True
            self._processed_callbacks[callback_id] = True
            while len(self._processed_callbacks) > cap:
                self._processed_callbacks.popitem(last=False)
            return False

    # ---- poller 觀測 ----

    def mark_poller(self, running: bool) -> None:
        with self._lock:
            self.poller_running = running

    def mark_poll(self, at: datetime) -> None:
        with self._lock:
            self.last_poll_at = at

    def count_ignored_foreign_update(self) -> None:
        with self._lock:
            self.ignored_foreign_updates += 1


class ChatState:
    """Telegram 對話的程序內計數（同時只允許一個提問在途）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.in_flight: bool = False
        self.answered: int = 0

    def begin_ask(self) -> bool:
        """搶下唯一的提問名額；已經有人在問就回 False。"""
        with self._lock:
            if self.in_flight:
                return False
            self.in_flight = True
            return True

    def finish_ask(self, *, answered: bool) -> None:
        with self._lock:
            self.in_flight = False
            if answered:
                self.answered += 1

    def snapshot(self) -> Tuple[bool, int]:
        with self._lock:
            return self.in_flight, self.answered


class TtlCache:
    """單鍵 TTL 快取：鍵一換就整份作廢（問候卡 LLM 文字與 advisor 摘要共用）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: Optional[str] = None
        self._value: Any = None
        self._expires_at: Optional[datetime] = None

    def get(self, key: str, now: datetime) -> Any:
        with self._lock:
            if (
                self._key == key
                and self._value is not None
                and self._expires_at is not None
                and now < self._expires_at
            ):
                return self._value
            return None

    def put(self, key: str, value: Any, now: datetime, ttl: timedelta) -> None:
        if ttl.total_seconds() <= 0:
            return
        with self._lock:
            self._key = key
            self._value = value
            self._expires_at = now + ttl

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._value = None
            self._expires_at = None


class ProjectCache:
    """專案清單的 TTL 快取——擋住前端每 4 秒輪詢時的三表全量查詢。

    **時間戳與列表是兩件事**，與 D11 之前逐字相同：`refresh_project_states()` 只看時間戳
    （即使列表是空的也不再重算），`get_active_projects_list()` 要時間戳夠新**而且**列表非空。
    這個區分看起來多餘，但它是現行行為；要改就另開一輪帶自己的收據。

    `refresh_lock` 對外開放：同一個服務的多個請求可能同時發現過期，重整流程要互斥，
    否則兩邊都會先讀到「尚未建立」而競爭寫入同一個 project_key。
    """

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self.ttl_seconds = ttl_seconds
        self.refresh_lock = threading.RLock()
        self._rows: List[Dict[str, Any]] = []
        self._states_refreshed_at: float = 0.0

    def states_fresh(self, clock_now: float) -> bool:
        return (clock_now - self._states_refreshed_at) < self.ttl_seconds

    def mark_states_refreshed(self, clock_now: float) -> None:
        self._states_refreshed_at = clock_now

    def rows_if_fresh(self, clock_now: float) -> Optional[List[Dict[str, Any]]]:
        if self._rows and (clock_now - self._states_refreshed_at) < self.ttl_seconds:
            return self._rows
        return None

    def put_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def invalidate(self) -> None:
        with self.refresh_lock:
            self._rows = []
            self._states_refreshed_at = 0.0


@dataclass
class RuntimeState:
    """一個服務程序的全部可變執行期狀態。"""

    confirms: ConfirmStore = field(default_factory=ConfirmStore)
    approvals: ApprovalState = field(default_factory=ApprovalState)
    chat: ChatState = field(default_factory=ChatState)
    greeting_cache: TtlCache = field(default_factory=TtlCache)
    advisor_cache: TtlCache = field(default_factory=TtlCache)
    projects: ProjectCache = field(default_factory=ProjectCache)

    def clear(self) -> None:
        """整份歸零——等同重啟服務（測試偶爾需要，產品程式碼不該呼叫）。"""
        self.confirms.clear()
        self.approvals.disarm()
        self.greeting_cache.clear()
        self.advisor_cache.clear()
        self.projects.invalidate()


_PROCESS_STATE = RuntimeState()


def runtime_state() -> RuntimeState:
    """這個程序的預設狀態。呼叫端不給 store 時就用它。"""
    return _PROCESS_STATE


def new_runtime_state() -> RuntimeState:
    """一份全新的狀態——測試用它取代「重設鉤子」。"""
    return RuntimeState()
