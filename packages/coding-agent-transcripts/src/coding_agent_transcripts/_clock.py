"""現在幾點。

**刻意自己寫一份，不從使用端借。** 本專案原本是 `from core.time_utils import get_local_now`
（一行 `datetime.now()`）。一個解析函式庫不該為了取現在幾點而相依一個活動追蹤器——
那條相依是 ADR-033 查證「能不能獨立」時唯一擋路的兩件事之一。

所有 `discover`／`parse` 都把它當**預設參數**（`now=get_local_now`），
所以呼叫端隨時能換掉；測試靠這一點釘住時鐘，不必 monkeypatch 模組。
"""

from __future__ import annotations

from datetime import datetime


def get_local_now() -> datetime:
    """本地時間，無時區（全套件一致，不混用 aware／naive）。"""
    return datetime.now()
