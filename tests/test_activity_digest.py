"""每日工作誌：把採集器看到的一天變成小秘書記得住的觀察。

- 只 reduce 既有資料：可回溯計數 ＋ 已保存的時段微摘要；**不呼叫任何 LLM**。
- 不新增資料類別：不存 prompt／response 原文（ADR-012 邊界不變）。
- 寫出的是 observation：每天每種一則（source_ref 去重）、可刪、會過期。
- 沒有微摘要就只寫計數，並如實說明只有計數——不編故事。
- 什麼都沒採集到的那天不寫筆記，也不假裝有事發生。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from core.activity_digest import (
    build_daily_digest,
    collect_day_stats,
    compose_day_body,
    micro_highlights,
    per_project_counts,
)
from core.models import (
    ActivityMicroSummary,
    AIPromptEvent,
    Base,
    FileActivityEvent,
    GitActivityEvent,
    OpenLoop,
    ProjectState,
    SecretaryNote,
)

NOW = datetime(2026, 9, 5, 8, 0)          # 早上跑早晨包
DAY = datetime(2026, 9, 4, 0, 0)          # 要彙整的「昨天」


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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def db():
    return TempDatabase()


@pytest.fixture
def cfg():
    return DictConfig()


def _at(hour: int, day: datetime = DAY) -> datetime:
    return day + timedelta(hours=hour)


def _seed_day(db, *, with_micro=True):
    """昨天：uavMonitor 2 commit＋3 輪 AI、論文 2 個 .tex、omnicontext 1 commit。"""
    with db.session_scope() as s:
        for i in range(2):
            s.add(GitActivityEvent(timestamp=_at(9 + i), repo_name="uavMonitor", repo_path="/r/uav",
                                   commit_hash=f"u{i}", message="sync", insertions=5, deletions=1))
        s.add(GitActivityEvent(timestamp=_at(14), repo_name="omnicontext", repo_path="/r/omni",
                               commit_hash="o1", message="fix", insertions=3, deletions=0))
        for i in range(3):
            s.add(AIPromptEvent(timestamp=_at(10, DAY) + timedelta(minutes=i),
                                platform="antigravity", prompt_text="幫我更新同步這個專案 本地雲端更新",
                                project_tag="uavMonitor"))
        for i in range(2):
            s.add(FileActivityEvent(timestamp=_at(16) + timedelta(minutes=i), file_path=f"/p/ch{i}.tex",
                                    file_name=f"ch{i}.tex", file_type=".tex", action="modified",
                                    project_name="論文"))
        s.add(ProjectState(project_key="uavMonitor", display_name="uavMonitor", status="active",
                           last_activity_at=_at(14), last_action_summary="sync", category="dev"))
        s.add(OpenLoop(project_key="uavMonitor", title="done", status="resolved",
                       resolved_at=_at(15), updated_at=_at(15)))
        # 前天的活動不該被算進來
        s.add(GitActivityEvent(timestamp=_at(9, DAY - timedelta(days=1)), repo_name="old",
                               repo_path="/r/old", commit_hash="x", message="old"))
        if with_micro:
            s.add(ActivityMicroSummary(period_start=_at(9), period_end=_at(11), provider="ollama",
                                       summary_text="同步 uavMonitor 本地與雲端，修掉兩個匯入錯誤",
                                       event_count=5))
            s.add(ActivityMicroSummary(period_start=_at(16), period_end=_at(18), provider="ollama",
                                       summary_text="改論文第三章圖表與參考文獻", event_count=2))
    return db


# ---- 統計只算那一天 ----


def test_stats_cover_exactly_the_target_day(db, cfg):
    stats = collect_day_stats(DAY.date(), database=_seed_day(db), cfg=cfg)
    assert stats["commits"] == 3            # 前天那筆不算
    assert sorted(stats["commit_repos"]) == ["omnicontext", "uavMonitor"]
    assert stats["ai_turns"] == 3 and stats["files_changed"] == 2
    assert stats["loops_resolved"] == 1


def test_per_project_counts_group_by_real_attribution(db):
    rows = {r["project"]: r for r in per_project_counts(DAY.date(), database=_seed_day(db))}
    assert rows["uavMonitor"]["commits"] == 2 and rows["uavMonitor"]["ai_turns"] == 3
    assert rows["論文"]["files"] == 2
    assert rows["omnicontext"]["commits"] == 1


def test_unattributed_activity_is_not_guessed_into_a_project(db):
    with db.session_scope() as s:
        s.add(AIPromptEvent(timestamp=_at(11), platform="chatgpt", prompt_text="q", project_tag=None))
        s.add(FileActivityEvent(timestamp=_at(11), file_path="/x.py", file_name="x.py",
                                file_type=".py", action="modified", project_name=None))
    assert per_project_counts(DAY.date(), database=db) == []


# ---- 重點來自既有微摘要，不重新生成 ----


def test_highlights_reuse_stored_micro_summaries_in_time_order(db):
    texts = micro_highlights(DAY.date(), database=_seed_day(db))
    assert texts == [
        "同步 uavMonitor 本地與雲端，修掉兩個匯入錯誤",
        "改論文第三章圖表與參考文獻",
    ]


def test_digest_never_calls_an_llm(db, cfg, monkeypatch):
    import synthesizer.micro_summarizer as micro

    def explode(*_args, **_kwargs):
        raise AssertionError("每日工作誌不得呼叫 LLM")

    monkeypatch.setattr(micro, "generate_micro_summary", explode)
    receipt = build_daily_digest(days_back=1, database=_seed_day(db), cfg=cfg, now=NOW)
    assert receipt["llm_used"] is False and receipt["status"] == "ok"


def test_module_source_declares_no_llm_or_network_use():
    from core import activity_digest

    text = open(activity_digest.__file__, encoding="utf-8").read()
    for forbidden in ("llm_gateway", "import requests", "import httpx", "urllib.request", "subprocess"):
        assert forbidden not in text, f"每日工作誌不得使用 {forbidden}"


# ---- 正文：數字對得上，沒有微摘要就說沒有 ----


def test_body_mentions_the_counts_and_the_highlights(db, cfg):
    receipt = build_daily_digest(days_back=1, database=_seed_day(db), cfg=cfg, now=NOW, write_memory=False)
    body = receipt["text"]
    assert "3 個 commit" in body and "3 輪 AI 對話" in body
    assert "改了 2 個檔案" in body and "論文文檔 2" in body
    assert "同步 uavMonitor 本地與雲端" in body


def test_body_says_it_only_has_counts_when_no_micro_summary_exists(db, cfg):
    receipt = build_daily_digest(
        days_back=1, database=_seed_day(db, with_micro=False), cfg=cfg, now=NOW, write_memory=False
    )
    assert "只有計數" in receipt["text"]
    assert receipt["micro_summaries"] == 0


def test_a_day_with_nothing_observed_says_so_and_writes_nothing(db, cfg):
    receipt = build_daily_digest(days_back=1, database=db, cfg=cfg, now=NOW)
    assert receipt["observed_anything"] is False
    assert receipt["notes_written"] == 0
    assert "不代表你沒做事" in receipt["text"]


# ---- 寫進記憶區：可刪、去重、有專案層 ----


def _notes(db):
    with db.session_scope() as s:
        return [
            {"kind": n.kind, "title": n.title, "body": n.body, "project_key": n.project_key,
             "source": n.source, "source_ref": n.source_ref}
            for n in s.query(SecretaryNote).order_by(SecretaryNote.id).all()
        ]


def test_digest_writes_one_day_note_plus_project_notes(db, cfg):
    receipt = build_daily_digest(days_back=1, database=_seed_day(db), cfg=cfg, now=NOW)
    notes = _notes(db)

    assert receipt["notes_written"] == len(notes)
    assert all(n["kind"] == "observation" and n["source"] == "daily_digest" for n in notes)
    day_note = next(n for n in notes if n["source_ref"] == "daily_digest:2026-09-04")
    assert day_note["project_key"] is None and "2026-09-04 你做了" in day_note["body"]
    projects = {n["project_key"] for n in notes if n["project_key"]}
    assert "uavMonitor" in projects and "論文" in projects
    # 只有 1 筆事件的專案不佔一則記憶
    assert "omnicontext" not in projects


def test_rerunning_the_same_day_does_not_duplicate_notes(db, cfg):
    seeded = _seed_day(db)
    first = build_daily_digest(days_back=1, database=seeded, cfg=cfg, now=NOW)
    before = len(_notes(db))
    second = build_daily_digest(days_back=1, database=seeded, cfg=cfg, now=NOW)

    assert first["notes_written"] == before
    assert second["notes_written"] == 0      # 同一天重跑不再寫
    assert len(_notes(db)) == before


def test_digest_does_not_store_prompt_or_response_text(db, cfg):
    """ADR-012 邊界：記憶區不存 prompt／response 原文。"""
    build_daily_digest(days_back=1, database=_seed_day(db), cfg=cfg, now=NOW)
    for note in _notes(db):
        assert "幫我更新同步這個專案 本地雲端更新" not in note["body"]


def test_digest_can_be_turned_off(db, cfg):
    cfg.data = {"proactive_secretary": {"daily_digest": {"enabled": False}}}
    receipt = build_daily_digest(days_back=1, database=_seed_day(db), cfg=cfg, now=NOW)
    assert receipt["status"] == "disabled" and receipt["notes_written"] == 0
    assert _notes(db) == []


def test_digest_writes_nothing_when_memory_is_disabled(db, cfg):
    cfg.data = {"secretary_memory": {"enabled": False}}
    receipt = build_daily_digest(days_back=1, database=_seed_day(db), cfg=cfg, now=NOW)
    assert receipt["memory"] == "disabled" and receipt["notes_written"] == 0
    assert _notes(db) == []


def test_digest_leaves_the_captured_events_untouched(db, cfg):
    seeded = _seed_day(db)
    with seeded.session_scope() as s:
        before = {
            table.name: s.execute(func.count().select().select_from(table)).scalar()
            for table in Base.metadata.sorted_tables if table.name != "secretary_notes"
        }
    build_daily_digest(days_back=1, database=seeded, cfg=cfg, now=NOW)
    with seeded.session_scope() as s:
        after = {
            table.name: s.execute(func.count().select().select_from(table)).scalar()
            for table in Base.metadata.sorted_tables if table.name != "secretary_notes"
        }
    assert before == after


# ---- 排程契約 ----


def test_daily_digest_is_registered_as_a_read_only_schedulable_template():
    from core.scheduled_tasks import SCHEDULABLE_TEMPLATES

    template = SCHEDULABLE_TEMPLATES["daily_digest"]
    assert template.risk_level == "L0_READ_ONLY"
    assert template.validate_params({"days_back": 3}) == {"days_back": 3}


def test_days_back_outside_the_allowed_range_is_rejected():
    from core.agent_executor import ExecutionRejected
    from core.scheduled_tasks import SCHEDULABLE_TEMPLATES

    validate = SCHEDULABLE_TEMPLATES["daily_digest"].validate_params
    with pytest.raises(ExecutionRejected):
        validate({"days_back": 30})
    with pytest.raises(ExecutionRejected):
        validate({"days_back": "昨天"})
