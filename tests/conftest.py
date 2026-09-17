"""每個測試拿到一份全新的程序內狀態（ADR-027，TODO D11）。

D11 之前，五個模組各自帶一個「測試用重設鉤子」——`_reset_state_for_tests()`（兩個）、
`_reset_llm_cache_for_tests()`、`_reset_pending_confirms()`、`reset_advisor_cache()`。
那些函式住在**產品程式碼**裡（進 wheel、誰都能呼叫），而且各自只清得掉自己記得要清的欄位：
漏一個的症狀是「單獨跑會過、一起跑會壞」，而且很難查。

現在狀態是 `core/runtime_state.RuntimeState` 的一個物件，所以「重設」就是**換一個新的**：
下面這個 autouse fixture 每個測試換一次，沒有人需要記得呼叫，也不可能漏欄位。
`monkeypatch` 會在測試結束時自動還原。

要測「兩份狀態互不影響」的測試不必靠這個 fixture——直接建兩個 `RuntimeState()` 傳進去就好。
"""

from __future__ import annotations

import pytest

import core.runtime_state as runtime_state_module
from core.runtime_state import new_runtime_state


@pytest.fixture(autouse=True)
def fresh_runtime_state(monkeypatch):
    """把行程預設換成一份全新的狀態，測試之間不再互相污染。"""
    state = new_runtime_state()
    monkeypatch.setattr(runtime_state_module, "_PROCESS_STATE", state)
    return state
