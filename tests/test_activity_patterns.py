"""模式感知提案（ADR-017）：秘書開始用它記得的東西。

- 模式只來自（專案 × 日）的活動計數，**今天不算**（分母只到現在，與 A1 同一個教訓）。
- 沒歸戶的活動只算「有活動」，不猜專案。
- no_daily_routine：近一週有在工作、沒有每日排程才提；建了排程就消失。
- neglected_active_project：前一週活躍、近一週歸零；已有未結事項提案的專案不重複提。
- 習慣加權是排序不是新提案：主線專案的既有訊號加分並附理由，分數不超過 1.0。
- 全部走既有的 snooze／mute／diversity；模式層壞掉不得拖垮提案清單。
- 可執行動作只對應既有 template：被冷落的專案給 L0 Handoff，no_daily_routine 不給。
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import activity_patterns as ap
from core.activity_patterns import (
    activity_matrix,
    apply_habit_boost,
    collect_pattern_signals,
    routine_schedules_present,
)
from core.models import (
    AIPromptEvent,
    Base,
    FileActivityEvent,
    GitActivityEvent,
    OpenLoop,
    ProjectState,
    SecretaryNote,
    SecretaryScheduledTask,
)
from core.proactive_secretary import build_action_proposals

NOW = datetime(2026, 9, 15, 10, 0)            # 週二早上
YESTERDAY = NOW.date() - timedelta(days=1)    # 「近一週」的最後一天


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
    # 提案引擎本身的開關要開；patterns 用預設值
    return DictConfig({"proactive_secretary": {"enabled": True, "max_proposals": 12}})


def _day(offset: int, hour: int = 10) -> datetime:
    """相對於 NOW 的第 offset 天（1 = 昨天）。"""
    return datetime.combine(NOW.date() - timedelta(days=offset), datetime.min.time()) + timedelta(hours=hour)


_COMMIT_SEQ = itertools.count(1)


def _commit(db, project: str, when: datetime):
    # commit_hash 有 UNIQUE 約束；同一天多筆 commit 用流水號保證不撞
    with db.session_scope() as s:
        s.add(GitActivityEvent(timestamp=when, repo_name=project, repo_path=f"/r/{project}",
                               commit_hash=f"{project}-{next(_COMMIT_SEQ):06d}", message="m"))


def _ai(db, project, when: datetime):
    with db.session_scope() as s:
        s.add(AIPromptEvent(timestamp=when, platform="antigravity", prompt_text="幫我同步", project_tag=project))


def _file(db, project, when: datetime):
    with db.session_scope() as s:
        s.add(FileActivityEvent(timestamp=when, file_path="/p/a.tex", file_name="a.tex",
                                file_type=".tex", action="modified", project_name=project))


def _active_on(db, project: str, offsets: list[int]):
    for offset in offsets:
        _commit(db, project, _day(offset))


def _schedule(db, template_id="morning_pack", enabled=True):
    with db.session_scope() as s:
        s.add(SecretaryScheduledTask(template_id=template_id, schedule_kind="daily",
                                     run_time="07:30", enabled=enabled))


# ---- 活動矩陣 ----


def test_matrix_groups_by_project_and_day_and_never_guesses_a_project(db):
    _commit(db, "uav", _day(1))
    _commit(db, "uav", _day(1, 15))     # 同一天兩筆只算一天
    _ai(db, "uav", _day(3))
    _file(db, "論文", _day(2))
    _ai(db, None, _day(4))              # 沒歸戶：只算「有活動」

    matrix = activity_matrix(end_day=YESTERDAY, days=7, database=db)
    assert matrix["uav"] == {_day(1).date(), _day(3).date()}
    assert matrix["論文"] == {_day(2).date()}
    assert _day(4).date() in matrix["*"]
    assert "" not in matrix and None not in matrix


def test_today_is_never_counted(db, cfg):
    """今天的活動不進任何模式——今天的分母只到現在。"""
    _active_on(db, "uav", [0, 0, 0])   # 只有今天
    signals, meta = collect_pattern_signals(database=db, cfg=cfg, now=NOW)
    assert meta["recent_active_days"] == 0
    assert signals == []


# ---- S1 no_daily_routine ----


def test_no_daily_routine_fires_when_working_without_a_schedule(db, cfg):
    _active_on(db, "uav", [1, 2, 3, 5])
    _file(db, "論文", _day(2))
    signals, meta = collect_pattern_signals(database=db, cfg=cfg, now=NOW)

    routine = [s for s in signals if s["signal_type"] == "no_daily_routine"]
    assert len(routine) == 1
    assert "4 天" in routine[0]["title"] and "uav" in routine[0]["title"]
    assert routine[0]["project_key"] == "OmniContext"
    assert meta["routine_schedules"] == []


def test_no_daily_routine_disappears_once_a_schedule_exists(db, cfg):
    _active_on(db, "uav", [1, 2, 3, 5])
    _schedule(db, "morning_pack")
    signals, meta = collect_pattern_signals(database=db, cfg=cfg, now=NOW)
    assert not [s for s in signals if s["signal_type"] == "no_daily_routine"]
    assert meta["routine_schedules"] == ["morning_pack"]


def test_disabled_schedule_does_not_count_as_a_routine(db):
    _schedule(db, "daily_digest", enabled=False)
    assert routine_schedules_present(db) == set()


def test_no_daily_routine_needs_enough_active_days(db, cfg):
    _active_on(db, "uav", [1, 2, 3])   # 只有 3 天，門檻 4
    signals, _ = collect_pattern_signals(database=db, cfg=cfg, now=NOW)
    assert not [s for s in signals if s["signal_type"] == "no_daily_routine"]


# ---- S2 neglected_active_project ----


def test_neglected_project_fires_when_last_week_was_busy_and_this_week_is_silent(db, cfg):
    _active_on(db, "thesis", [8, 9, 11, 13])   # 前一週 4 天
    signals, _ = collect_pattern_signals(database=db, cfg=cfg, now=NOW)
    neglected = [s for s in signals if s["signal_type"] == "neglected_active_project"]
    assert len(neglected) == 1
    assert neglected[0]["project_key"] == "thesis"
    assert "4 天" in neglected[0]["title"]
    assert neglected[0]["score"] == pytest.approx(0.7)


def test_neglected_project_does_not_fire_if_touched_this_week(db, cfg):
    _active_on(db, "thesis", [8, 9, 11, 13, 2])
    signals, _ = collect_pattern_signals(database=db, cfg=cfg, now=NOW)
    assert not [s for s in signals if s["signal_type"] == "neglected_active_project"]


def test_neglected_project_is_skipped_when_an_open_loop_already_covers_it(db, cfg):
    _active_on(db, "thesis", [8, 9, 11])
    signals, _ = collect_pattern_signals(database=db, cfg=cfg, now=NOW, exclude_projects={"thesis"})
    assert not [s for s in signals if s["signal_type"] == "neglected_active_project"]


def test_pattern_signals_cite_daily_digest_notes_as_evidence(db, cfg):
    _active_on(db, "thesis", [8, 9, 11])
    with db.session_scope() as s:
        for offset in (9, 11):
            day = (NOW.date() - timedelta(days=offset)).isoformat()
            s.add(SecretaryNote(kind="observation", body="x", source="daily_digest",
                                source_ref=f"daily_digest:{day}:thesis", project_key="thesis",
                                created_at=_day(offset - 1, 7)))
    signals, _ = collect_pattern_signals(database=db, cfg=cfg, now=NOW)
    refs = [e["source_ref"] for e in signals[0]["evidence_extra"]]
    assert len(refs) == 2 and all(r.startswith("daily_digest:") and r.endswith(":thesis") for r in refs)


# ---- S3 習慣加權 ----


def test_habit_boost_raises_main_line_projects_and_explains_why(cfg):
    signals = [
        {"signal_type": "repo_needs_pull", "project_key": "uav", "score": 0.55, "reasons": ["落後"]},
        {"signal_type": "repo_needs_pull", "project_key": "old", "score": 0.55, "reasons": ["落後"]},
        {"signal_type": "aging_pr", "project_key": "uav", "score": 0.95, "reasons": []},
    ]
    boosted = apply_habit_boost(signals, cfg=cfg, recent_active={"uav": 5, "old": 1})
    assert boosted == 2
    assert signals[0]["score"] == pytest.approx(0.70) and signals[0]["habit_boosted"] is True
    assert "5 天在動" in signals[0]["reasons"][-1]
    assert signals[1]["score"] == pytest.approx(0.55) and "habit_boosted" not in signals[1]
    assert signals[2]["score"] == 1.0                     # 不超過 1.0


def test_habit_boost_never_touches_pattern_signals_themselves(cfg):
    signals = [{"signal_type": "neglected_active_project", "project_key": "uav", "score": 0.6, "reasons": []}]
    assert apply_habit_boost(signals, cfg=cfg, recent_active={"uav": 7}) == 0
    assert signals[0]["score"] == 0.6


def test_habit_boost_can_be_turned_off(db):
    cfg = DictConfig({"proactive_secretary": {"patterns": {"habit_boost": 0}}})
    signals = [{"signal_type": "repo_needs_pull", "project_key": "uav", "score": 0.5, "reasons": []}]
    assert apply_habit_boost(signals, cfg=cfg, recent_active={"uav": 7}) == 0


# ---- 整合進提案引擎 ----


def _proposals(db, cfg, **kw):
    return build_action_proposals(database=db, cfg=cfg, now=NOW,
                                  extension_status={"extension": {"token_configured": False}}, **kw)


def test_engine_emits_pattern_proposals_with_reason_action_and_why_now(db, cfg):
    _active_on(db, "uav", [1, 2, 3, 5])
    _active_on(db, "thesis", [8, 9, 11])
    result = _proposals(db, cfg)
    by_type = {p["proposal_type"]: p for p in result["proposals"]}

    assert {"no_daily_routine", "neglected_active_project"} <= set(by_type)
    routine = by_type["no_daily_routine"]
    assert "建立每日排程" in routine["suggested_action"] and routine["why_now"]
    assert routine["execution_available"] is False and routine["risk_level"] == "L0_READ_ONLY"
    neglected = by_type["neglected_active_project"]
    assert neglected["project_key"] == "thesis" and "Handoff" in neglected["suggested_action"]
    assert result["inputs"]["patterns"]["used"] is True
    assert result["inputs"]["patterns"]["recent_active_by_project"] == {"uav": 4}


def test_engine_does_not_duplicate_a_project_that_already_has_an_open_loop_proposal(db, cfg):
    _active_on(db, "thesis", [8, 9, 11])
    with db.session_scope() as s:
        s.add(ProjectState(project_key="thesis", display_name="thesis", status="stale",
                           last_activity_at=_day(8), last_action_summary="x", category="research"))
        s.add(OpenLoop(project_key="thesis", title="finish ch3", status="open",
                       created_at=_day(9), updated_at=_day(9)))
    result = _proposals(db, cfg)
    types_for_thesis = [p["proposal_type"] for p in result["proposals"] if p["project_key"] == "thesis"]
    assert "neglected_active_project" not in types_for_thesis
    assert types_for_thesis  # 但未結事項的提案還在


def test_pattern_proposals_respect_mute_preferences(db, cfg):
    _active_on(db, "uav", [1, 2, 3, 5])
    with db.session_scope() as s:
        s.add(SecretaryNote(kind="preference", body="不要提醒 no_daily_routine", source="web"))
    result = _proposals(db, cfg)
    assert not [p for p in result["proposals"] if p["proposal_type"] == "no_daily_routine"]
    assert result["inputs"]["memory_muted"] >= 1


def test_habit_boost_reorders_existing_repo_signals_inside_the_engine(db, cfg, monkeypatch):
    _active_on(db, "uav", [1, 2, 3, 4, 5])
    _schedule(db, "morning_pack")   # 讓 no_daily_routine 不出現，專心看排序
    now = NOW

    def fake_repo_signals(*, cfg=None, now=None):
        base = {"subject_ref": "repo:x", "observed_at": now, "url": None, "age_days": 0.1, "open_loop_refs": [],
                "detail": "", "reasons": ["落後"], "score": 0.55}
        return [
            {**base, "signal_type": "repo_needs_pull", "project_key": "old", "evidence_ref": "repo_sync_snapshot:1",
             "subject_ref": "repo:1", "title": "old 落後"},
            {**base, "signal_type": "repo_needs_pull", "project_key": "uav", "evidence_ref": "repo_sync_snapshot:2",
             "subject_ref": "repo:2", "title": "uav 落後"},
        ], {"used": True}

    import core.repo_sync_report as rsr
    monkeypatch.setattr(rsr, "collect_repo_sync_signals", fake_repo_signals)
    result = _proposals(db, cfg)
    pulls = [p for p in result["proposals"] if p["proposal_type"] == "repo_needs_pull"]
    assert [p["project_key"] for p in pulls] == ["uav", "old"]      # 主線排前面
    assert pulls[0]["habit_boosted"] is True and pulls[0]["score"] == pytest.approx(0.70)
    assert "主線" in pulls[0]["reason"]
    assert result["inputs"]["patterns"]["habit_boosted"] == 1


def test_pattern_layer_failure_does_not_break_the_proposal_list(db, cfg, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("matrix exploded")

    monkeypatch.setattr(ap, "collect_pattern_signals", boom)
    result = _proposals(db, cfg)
    assert result["status"] == "proposal_only"
    assert result["inputs"]["patterns"] == {"used": False, "reason": "error:RuntimeError"}


def test_patterns_can_be_disabled(db):
    cfg = DictConfig({"proactive_secretary": {"enabled": True, "patterns": {"enabled": False}}})
    _active_on(db, "uav", [1, 2, 3, 5])
    result = _proposals(db, cfg)
    assert not [p for p in result["proposals"] if p["proposal_type"] in ("no_daily_routine", "neglected_active_project")]
    assert result["inputs"]["patterns"]["reason"] == "disabled"


# ---- 可執行動作只對應既有 template ----


def test_neglected_project_gets_the_existing_l0_handoff_and_routine_gets_none():
    from core.agent_executor import ExecutorServices, derive_action

    services = ExecutorServices()
    neglected = {"proposal_type": "neglected_active_project", "project_key": "thesis", "subject_ref": "project:thesis"}
    plan = derive_action(neglected, services=services)
    assert plan is not None and plan.template_id == "generate_handoff" and plan.risk_level == "L0_READ_ONLY"

    routine = {"proposal_type": "no_daily_routine", "project_key": "OmniContext", "subject_ref": "schedule:daily_routine"}
    assert derive_action(routine, services=services) is None


def test_module_never_reads_prompt_text_or_calls_an_llm():
    text = open(ap.__file__, encoding="utf-8").read()
    for forbidden in ("prompt_text", "response_text", "llm_gateway", "import requests", "import httpx", "subprocess"):
        assert forbidden not in text, f"模式層不得使用 {forbidden}"
