"""活動聚合只有一份來源定義（TODO D3，ROADMAP §13 R1）。

以前「哪些表算活動、專案名在哪個欄位、一天從哪到哪」寫在三個模組裡各一遍，
四個使用它們的地方（每週回顧、模式提案、每日工作誌、問候卡）**本來就該講同一組
數字，但沒有任何東西保證**。這裡把那個保證寫成測試：

1. 同一天的同一批事件，四處算出來的數字必須互相對得上。
2. 歸戶規則只有一套：沒歸戶的活動只進「任何活動」，不猜專案；空白與純空白一視同仁。
3. 日界只有一套：``[當日 00:00, 隔日 00:00)`` 半開區間，午夜前後不會重複計入。
4. 要增減活動來源就只改 ``EVENT_SOURCES``——四處一起變（掃原始碼把關）。
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.activity_digest import collect_day_stats, per_project_counts
from core.activity_patterns import activity_matrix
from core.activity_sources import (
    ANY_PROJECT,
    EVENT_KINDS,
    EVENT_SOURCES,
    day_bounds,
    normalize_project,
    project_activity_matrix,
    project_event_counts,
    source_for,
)
from core.models import AIPromptEvent, Base, FileActivityEvent, GitActivityEvent
from core.secretary.greeting import collect_activity_stats
from core.weekly_review import active_days_by_project

ROOT = Path(__file__).resolve().parents[1]
DAY = datetime(2026, 9, 4, 0, 0)  # 要對帳的那一天
NEXT_DAY_MIDNIGHT = DAY + timedelta(days=1)


class DictConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class TempDatabase:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()


@pytest.fixture
def db():
    """那一天：alpha 有 2 commit ＋ 1 AI ＋ 1 檔案；beta 只有 1 AI；另有 1 筆沒歸戶。"""
    database = TempDatabase()
    with database.session_scope() as session:
        session.add_all([
            GitActivityEvent(repo_name="alpha", repo_path="/r/alpha", commit_hash="a1", message="m",
                             timestamp=DAY + timedelta(hours=9)),
            GitActivityEvent(repo_name="alpha", repo_path="/r/alpha", commit_hash="a2", message="m",
                             timestamp=DAY + timedelta(hours=14)),
            AIPromptEvent(platform="claude_code", project_tag="alpha", prompt_text="p",
                          timestamp=DAY + timedelta(hours=10)),
            AIPromptEvent(platform="codex", project_tag="beta", prompt_text="p",
                          timestamp=DAY + timedelta(hours=11)),
            AIPromptEvent(platform="codex", project_tag="   ", prompt_text="p",  # 沒歸戶：只進 "*"
                          timestamp=DAY + timedelta(hours=12)),
            FileActivityEvent(file_path="/a.py", file_name="a.py", project_name="alpha", file_type="py",
                              action="modified", timestamp=DAY + timedelta(hours=15)),
        ])
    return database


# ---- 1. 四處對帳 ----------------------------------------------------------


def test_the_same_day_adds_up_across_all_four_consumers(db):
    day = DAY.date()

    # (a) 模式提案用的矩陣
    matrix = activity_matrix(end_day=day, days=1, database=db)
    assert matrix[ANY_PROJECT] == {day}
    assert matrix["alpha"] == {day} and matrix["beta"] == {day}

    # (b) 每週回顧的活躍天數（單日區間）
    per_project, total_days = active_days_by_project(day, day, database=db)
    assert total_days == 1
    assert per_project == {"alpha": 1, "beta": 1}

    # (c) 每日工作誌的逐專案筆數
    rows = {row["project"]: row for row in per_project_counts(day, database=db)}
    assert set(rows) == {"alpha", "beta"}  # 沒歸戶的不列入逐專案
    assert rows["alpha"]["commits"] == 2 and rows["alpha"]["ai_turns"] == 1 and rows["alpha"]["files"] == 1
    assert rows["beta"]["ai_turns"] == 1 and rows["beta"]["commits"] == 0

    # (d) 問候卡／工作誌的當日總計：逐專案加總 ＋ 沒歸戶的那一筆
    stats = collect_day_stats(day, database=db, cfg=DictConfig())
    assert stats["commits"] == sum(r["commits"] for r in rows.values())
    assert stats["files_changed"] == sum(r["files"] for r in rows.values())
    assert stats["ai_turns"] == sum(r["ai_turns"] for r in rows.values()) + 1  # +1 = 沒歸戶那筆

    # 有筆數的專案，矩陣裡一定也有那一天——兩邊不可能各說各話
    assert set(rows) <= {p for p in matrix if p != ANY_PROJECT}


def test_unattributed_activity_counts_as_a_day_but_never_invents_a_project():
    database = TempDatabase()
    with database.session_scope() as session:
        session.add(AIPromptEvent(platform="codex", project_tag=None, prompt_text="p",
                                  timestamp=DAY + timedelta(hours=9)))
    day = DAY.date()
    matrix = project_activity_matrix(start_day=day, end_day=day, database=database)
    assert matrix[ANY_PROJECT] == {day}
    assert [p for p in matrix if p != ANY_PROJECT] == []
    assert project_event_counts(start_day=day, end_day=day, database=database) == {}
    assert active_days_by_project(day, day, database=database) == ({}, 1)


@pytest.mark.parametrize("raw,expected", [("alpha", "alpha"), ("  alpha  ", "alpha"), ("", ""), ("   ", ""), (None, "")])
def test_project_normalization_has_one_rule(raw, expected):
    assert normalize_project(raw) == expected


# ---- 2. 日界只有一套 ------------------------------------------------------


def test_day_boundary_is_half_open_so_midnight_belongs_to_the_next_day():
    database = TempDatabase()
    with database.session_scope() as session:
        session.add_all([
            GitActivityEvent(repo_name="alpha", repo_path="/r/alpha", commit_hash="late", message="m",
                             timestamp=NEXT_DAY_MIDNIGHT - timedelta(seconds=1)),
            GitActivityEvent(repo_name="alpha", repo_path="/r/alpha", commit_hash="next", message="m",
                             timestamp=NEXT_DAY_MIDNIGHT),
        ])
    day = DAY.date()
    start, end = day_bounds(day)
    assert start == DAY and end == NEXT_DAY_MIDNIGHT

    counts = project_event_counts(start_day=day, end_day=day, database=database)
    assert counts["alpha"]["commits"] == 1  # 隔日 00:00 那筆不算今天
    assert project_activity_matrix(start_day=day, end_day=day, database=database)["alpha"] == {day}
    tomorrow = NEXT_DAY_MIDNIGHT.date()
    assert project_event_counts(start_day=tomorrow, end_day=tomorrow, database=database)["alpha"]["commits"] == 1


def test_multi_day_window_includes_both_endpoints(db):
    day = DAY.date()
    matrix = project_activity_matrix(start_day=day - timedelta(days=3), end_day=day, database=db)
    assert matrix["alpha"] == {day}
    assert active_days_by_project(day - timedelta(days=3), day, database=db) == ({"alpha": 1, "beta": 1}, 1)


# ---- 3. 來源定義只有一份 --------------------------------------------------


def test_event_sources_are_the_single_definition_of_what_counts_as_activity():
    assert EVENT_KINDS == ("commits", "ai_turns", "files")
    assert {s.table for s in EVENT_SOURCES} == {
        "git_activity_events", "ai_prompt_events", "file_activity_events"
    }
    assert source_for("commits").model is GitActivityEvent
    assert source_for("ai_turns").model is AIPromptEvent
    assert source_for("files").model is FileActivityEvent
    with pytest.raises(KeyError):
        source_for("window_events")  # 前景時間不是「活動來源」，不得偷偷混進來


def test_greeting_reports_the_shared_table_names(db):
    stats = collect_day_stats(DAY.date(), database=db, cfg=DictConfig())
    assert stats["sources"]["commits"] == source_for("commits").table
    assert stats["sources"]["ai_turns"] == source_for("ai_turns").table
    assert stats["sources"]["files"] == source_for("files").table

    # 問候卡自己的視窗路徑（yesterday）也要得到同一組數字
    direct = collect_activity_stats(
        window="yesterday", now=NEXT_DAY_MIDNIGHT, database=db, cfg=DictConfig(), include_usage=False
    )
    for key in ("commits", "ai_turns", "files_changed"):
        assert direct[key] == stats[key], key


def test_consumers_do_not_query_the_event_tables_themselves():
    """四個使用端不得再自己對三張事件表做（專案 × 時間）查詢——那正是漂移的來源。"""
    pattern = re.compile(r"(GitActivityEvent|AIPromptEvent|FileActivityEvent)\.(repo_name|project_tag|project_name)")
    offenders = []
    # D8 之後問候卡在 core/secretary/greeting.py；其餘三個仍是 domain 模組
    for name in ("activity_patterns.py", "weekly_review.py", "activity_digest.py", "secretary/greeting.py"):
        text = (ROOT / "core" / name).read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(name)
    assert offenders == [], offenders
