"""「什麼算活動、專案名在哪個欄位、一天從哪到哪」——只定義一次（TODO D3，ROADMAP §13 R1）。

2026-09-16 之前這三件事散在四個模組裡各寫一遍：

- `core/activity_patterns.activity_matrix`：三張表依（專案 × 日）分組，取活躍天數。
- `core/activity_digest.per_project_counts`：**同樣三張表、同樣三個專案欄位**，但數的是筆數。
- `core/secretary_greeting.collect_activity_stats`：同樣三張表再查一次（另外還要 PR、
  專案狀態、未結事項與前景時間，所以它不只是「活動」）。
- `core/weekly_review.active_days_by_project`：已經委派給 `activity_patterns`（不是第四份）。

四處的**數字**本來就該一致，但沒有任何東西保證這件事：新增一種活動來源、或改掉
某張表的專案欄位，只會改到其中一兩處，而「每週回顧說你動了 3 天、工作誌說 0 個專案」
這種不一致沒有人會發現。這個模組把三件事收成單一定義：

- ``EVENT_SOURCES``：哪三張表算活動、各自的專案欄位與時間欄位。**要增減活動來源就改這裡。**
- ``normalize_project``：專案名正規化。空字串＝沒歸戶，**不猜**（只計入代表「任何活動」的 ``*``）。
- ``day_bounds``／``window_bounds``：日界一律 ``[當日 00:00, 隔日 00:00)``，半開區間。

刻意不收進來的：問候卡的 PR／專案狀態／未結事項／前景時間查詢。那些不是「活動來源」，
是另外幾張表各自的統計；硬合併只會讓這個模組變成第二個 god object。它們共用的是
``EVENT_SOURCES``（三張事件表要查哪個時間欄位），不是回傳值。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from sqlalchemy import func

from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent

ACTIVITY_CLAIM_BOUNDARY = (
    "活動只來自 git_activity_events／ai_prompt_events／file_activity_events 三張表的"
    "可回溯計數；沒歸戶的活動只計入「任何活動」，不猜專案。"
)


@dataclass(frozen=True)
class EventSource:
    """一種活動來源：哪張表、專案名在哪、時間在哪。"""

    kind: str  # "commits" / "ai_turns" / "files"——也是對外計數 dict 的鍵
    table: str  # 資料表名稱，用於 evidence 與 claim boundary
    model: Any
    project_column: Any
    timestamp_column: Any


EVENT_SOURCES: Tuple[EventSource, ...] = (
    EventSource("commits", "git_activity_events", GitActivityEvent,
                GitActivityEvent.repo_name, GitActivityEvent.timestamp),
    EventSource("ai_turns", "ai_prompt_events", AIPromptEvent,
                AIPromptEvent.project_tag, AIPromptEvent.timestamp),
    EventSource("files", "file_activity_events", FileActivityEvent,
                FileActivityEvent.project_name, FileActivityEvent.timestamp),
)

EVENT_KINDS: Tuple[str, ...] = tuple(source.kind for source in EVENT_SOURCES)
ANY_PROJECT = "*"  # 代表「這天有活動」，不指定專案


def source_for(kind: str) -> EventSource:
    for source in EVENT_SOURCES:
        if source.kind == kind:
            return source
    raise KeyError(f"未知的活動來源：{kind}")


def normalize_project(value: Any) -> str:
    """專案名正規化；回傳空字串代表**沒歸戶**——不猜、不回填。"""
    return str(value or "").strip()


def day_bounds(day: date) -> Tuple[datetime, datetime]:
    """一天的半開區間 ``[day 00:00, day+1 00:00)``。"""
    start = datetime.combine(day, dtime.min)
    return start, start + timedelta(days=1)


def window_bounds(start_day: date, end_day: date) -> Tuple[datetime, datetime]:
    """``start_day`` 到 ``end_day``（**含**）的半開區間。"""
    return datetime.combine(start_day, dtime.min), datetime.combine(end_day + timedelta(days=1), dtime.min)


def project_activity_matrix(
    *,
    start_day: date,
    end_day: date,
    database: Any | None = None,
) -> Dict[str, Set[date]]:
    """``start_day``～``end_day``（含）之間，每個專案哪幾天有活動。

    沒歸戶的活動只計入 ``"*"``，不猜專案；``"*"`` 一定存在（即使是空集合）。
    """
    database = database or get_db()
    since, until = window_bounds(start_day, end_day)
    matrix: Dict[str, Set[date]] = defaultdict(set)
    matrix[ANY_PROJECT]  # 保證存在

    with database.session_scope() as session:
        for source in EVENT_SOURCES:
            rows = (
                session.query(source.project_column, func.date(source.timestamp_column))
                .filter(source.timestamp_column >= since, source.timestamp_column < until)
                .group_by(source.project_column, func.date(source.timestamp_column))
                .all()
            )
            for project, day_text in rows:
                if not day_text:
                    continue
                try:
                    day = date.fromisoformat(str(day_text)[:10])
                except ValueError:
                    continue
                matrix[ANY_PROJECT].add(day)
                key = normalize_project(project)
                if key:
                    matrix[key].add(day)
    return dict(matrix)


def project_event_counts(
    *,
    start_day: date,
    end_day: date,
    database: Any | None = None,
) -> Dict[str, Dict[str, int]]:
    """``start_day``～``end_day``（含）之間，每個專案各類活動的**筆數**。

    與 :func:`project_activity_matrix` 用同一組來源與同一套歸戶規則——同一段期間，
    這裡數得出筆數的專案，那裡一定也有那幾天（有契約測試對帳）。沒歸戶的不列入。
    """
    database = database or get_db()
    since, until = window_bounds(start_day, end_day)
    buckets: Dict[str, Dict[str, int]] = {}

    with database.session_scope() as session:
        for source in EVENT_SOURCES:
            rows = (
                session.query(source.project_column, func.count(source.model.id))
                .filter(source.timestamp_column >= since, source.timestamp_column < until)
                .group_by(source.project_column)
                .all()
            )
            for project, count in rows:
                key = normalize_project(project)
                if not key:
                    continue  # 沒歸戶的活動不猜專案
                buckets.setdefault(key, {kind: 0 for kind in EVENT_KINDS})[source.kind] += int(count)
    return buckets


def active_days_in(
    matrix: Dict[str, Set[date]], *, start_day: date, end_day: date, include_any: bool = False
) -> Dict[str, int]:
    """把矩陣切到一個子區間，數每個專案的活躍天數。"""
    counted = {
        project: sum(1 for day in days if start_day <= day <= end_day)
        for project, days in matrix.items()
        if include_any or project != ANY_PROJECT
    }
    return counted


def within_window(column: Any, since: datetime, until: Optional[datetime]):
    """時間區間述詞；``until`` 為 None 代表開放到現在（問候卡的 today／2h 視窗）。"""
    return (column >= since) if until is None else ((column >= since) & (column < until))


def event_source_tables() -> Iterable[str]:
    return (source.table for source in EVENT_SOURCES)
