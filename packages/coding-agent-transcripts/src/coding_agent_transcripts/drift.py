"""漂移警示：檔案在動、事件是零（ADR-025，TODO D9）。

四種 transcript 都是別家工具的私有格式，沒有版本也沒有 schema。格式一變，parser 不會拋例外——
它會**正常跑完、產出零筆事件**，checkpoint 照常前移，diagnostics 一片 `healthy`。
`healthy` 只證明沒有拋例外，不證明有採集到東西。

這裡補上那個「證明有採集到東西」的最小判準：

    漂移 ⟺ 這次掃描看到的最新檔案 mtime 落在視窗內 ∧ 該平台在視窗內沒有任何事件

兩個條件缺一不可——沒用過的平台不會有檔案更新，所以「沒用」不會被誤報成「壞了」。

純函式：不碰資料庫、不碰檔案系統，兩個輸入都由呼叫端備妥。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, Optional

# 視窗要跨得過週末的使用空檔，又不能讓一個月的靜默無人發現。
# 刻意不開設定鍵：D6 才把 22 個調校鍵移出設定檔，這裡不該再加一個。
DRIFT_WINDOW_DAYS = 3


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


def evaluate_drift(
    *,
    newest_file_at: Mapping[str, Optional[datetime]],
    last_event_at: Mapping[str, Optional[datetime]],
    now: datetime,
    window_days: int = DRIFT_WINDOW_DAYS,
) -> Dict[str, Any]:
    """比對「檔案在動」與「事件是零」，回傳可直接放進 diagnostics 的區塊。

    `newest_file_at` 只包含**這次真的掃過的來源**：被設定關掉、目錄不存在或探索不到檔案的
    平台不會出現在裡面，因此也不會被警示。
    """
    cutoff = now - timedelta(days=window_days)
    platforms = []
    for platform, file_at in sorted(newest_file_at.items()):
        if not file_at or file_at < cutoff:
            continue  # 檔案沒在動 → 使用者只是沒用這個平台
        event_at = last_event_at.get(platform)
        if event_at and event_at >= cutoff:
            continue  # 視窗內有事件 → parser 還活著
        platforms.append(
            {
                "platform": platform,
                "newest_file_at": _isoformat(file_at),
                "last_event_at": _isoformat(event_at),
                "days_silent": (
                    round((now - event_at).total_seconds() / 86400.0, 1) if event_at else None
                ),
            }
        )

    return {
        "window_days": window_days,
        "checked_at": _isoformat(now),
        "platforms": platforms,
    }


def empty_drift(window_days: int = DRIFT_WINDOW_DAYS) -> Dict[str, Any]:
    """還沒掃過任何東西時的形狀——欄位齊全，內容誠實地是空的。"""
    return {"window_days": window_days, "checked_at": None, "platforms": []}
