"""每週回顧（ADR-020）：說的 vs 做的。

- 期間永遠是**已結束**的 ISO 週（週一～週日）；進行中的這週不算。
- 活躍天數只用（專案 × 日）計數；沒歸戶的活動只算整週天數，不猜專案。
- 說的 vs 做的：宣告的優先活躍 ≤ N 天、且同週有別的專案 ≥ M 天才算偏移；整週安靜不算；對不到名字如實說。
- 回顧寫成一則觀察，同一週只寫一次；沒活動不寫、關閉不寫、記憶區關閉不寫。
- priority_drift 訊號即時從表計算，進引擎後被宣告優先加分、頂掉同專案的「被冷落」、可 mute。
- 早晨包每天補上週的回顧且失敗不拖垮；template 是 L0；桌面「記得」剛出爐時優先挑回顧。
- 模組不讀 prompt、不呼叫 LLM。
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import weekly_review as wr
from core.acceptance import ITEM_IDS, build_acceptance_report
from core.models import AIPromptEvent, Base, GitActivityEvent, SecretaryNote, SecretaryScheduledTask
from core.proactive_secretary import SUGGESTED_ACTIONS, build_action_proposals, why_now
from core.secretary_home import pick_memory
from core.secretary_memory import add_note, record_observation
from core.weekly_review import (
    active_days_by_project,
    build_weekly_review,
    collect_priority_drift_signals,
    compare_said_vs_done,
    compose_review_body,
    digest_days_in,
    review_period,
    review_settings,
)

NOW = datetime(2026, 9, 16, 10, 0)          # 週三；上一個完整週 = 09-07（一）～09-13（日）= 2026-W37
WEEK_START, WEEK_END = date(2026, 9, 7), date(2026, 9, 13)


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


def _cfg(**over):
    data = {"proactive_secretary": {"enabled": True, "max_proposals": 12, "weekly_review": {}},
            "secretary_memory": {"enabled": True, "observation_ttl_days": 14}, "exporters": {"reports_dir": "/nonexistent"}}
    data["proactive_secretary"]["weekly_review"].update(over)
    return DictConfig(data)


_SEQ = itertools.count(1)


def _commit(db, project, day: date, hour=10):
    with db.session_scope() as s:
        s.add(GitActivityEvent(timestamp=datetime.combine(day, datetime.min.time()) + timedelta(hours=hour),
                               repo_name=project, repo_path=f"/r/{project}", commit_hash=f"{project}-{next(_SEQ):06d}", message="m"))


def _ai(db, project, day: date):
    with db.session_scope() as s:
        s.add(AIPromptEvent(timestamp=datetime.combine(day, datetime.min.time()) + timedelta(hours=14),
                            platform="antigravity", prompt_text="幫我同步", project_tag=project))


def _active(db, project, offsets):
    """offsets 是相對於上一個完整週週一的第幾天（0＝週一 … 6＝週日）。"""
    for offset in offsets:
        _commit(db, project, WEEK_START + timedelta(days=offset))


def _schedule(db):
    with db.session_scope() as s:
        s.add(SecretaryScheduledTask(template_id="morning_pack", schedule_kind="daily", run_time="07:30", enabled=True))


# ---- 期間 ----


def test_period_is_the_last_complete_iso_week_never_the_current_one():
    assert review_period(NOW) == (WEEK_START, WEEK_END, "2026-W37")
    assert review_period(datetime(2026, 9, 14, 7, 30)) == (WEEK_START, WEEK_END, "2026-W37")   # 週一早上：上週剛結束
    assert review_period(datetime(2026, 9, 13, 23, 0)) == (date(2026, 8, 31), date(2026, 9, 6), "2026-W36")   # 週日還沒結束
    assert review_period(NOW, weeks_back=2) == (date(2026, 8, 31), date(2026, 9, 6), "2026-W36")
    assert review_period(NOW, weeks_back=99)[2] == "2026-W34"   # 上限 4


# ---- 活躍天數與比較 ----


def test_active_days_count_per_project_and_unattributed_only_into_the_total(db):
    _active(db, "uav", [0, 1, 2, 3, 4])
    _active(db, "thesis", [5])
    _ai(db, None, WEEK_START + timedelta(days=6))          # 沒歸戶：只算整週天數
    _commit(db, "uav", WEEK_END + timedelta(days=1))       # 這週：不算
    per_project, total = active_days_by_project(WEEK_START, WEEK_END, database=db)
    assert per_project == {"uav": 5, "thesis": 1} and total == 7


def test_drift_when_a_declared_priority_idles_while_another_project_is_busy():
    cmp = compare_said_vs_done(active_days={"uav": 5, "thesis": 1, "omni": 3}, priorities=["Thesis"], settings=review_settings(_cfg()))
    assert cmp["aligned"] is False
    assert cmp["drift"][0]["project"] == "Thesis" and cmp["drift"][0]["matched_key"] == "thesis"   # 不分大小寫
    assert cmp["drift"][0]["days"] == 1 and cmp["drift"][0]["instead"] == {"project": "uav", "days": 5}


def test_a_quiet_week_is_not_drift_and_a_busy_priority_is_aligned():
    settings = review_settings(_cfg())
    quiet = compare_said_vs_done(active_days={"uav": 2, "thesis": 0}, priorities=["thesis"], settings=settings)
    assert quiet["drift"] == [] and quiet["aligned"] is None and quiet["declared"][0]["status"] == "quiet"
    done = compare_said_vs_done(active_days={"uav": 5, "thesis": 4}, priorities=["thesis"], settings=settings)
    assert done["drift"] == [] and done["aligned"] is True and done["declared"][0]["status"] == "done"
    none = compare_said_vs_done(active_days={"uav": 5}, priorities=[], settings=settings)
    assert none["declared"] == [] and none["aligned"] is None


def test_an_undeclared_busy_project_is_the_only_thing_that_counts_as_elsewhere():
    # 兩個宣告的優先：一個在做、一個沒做——「時間去了別處」只看未宣告的專案
    cmp = compare_said_vs_done(active_days={"uav": 5, "thesis": 0}, priorities=["uav", "thesis"], settings=review_settings(_cfg()))
    assert cmp["drift"] == [] and cmp["aligned"] is None
    assert [item["status"] for item in cmp["declared"]] == ["done", "quiet"]


def test_a_priority_name_that_matches_no_activity_is_reported_honestly():
    cmp = compare_said_vs_done(active_days={"uav": 5}, priorities=["論文"], settings=review_settings(_cfg()))
    assert cmp["declared"][0] == {"project": "論文", "matched_key": None, "days": 0, "status": "drift", "instead": {"project": "uav", "days": 5}}
    body = compose_review_body(label="2026-W37", start=WEEK_START, end=WEEK_END, total_days=5,
                               ranked=[{"project": "uav", "days": 5}], comparison=cmp, digest_days=3)
    assert "論文（0 天；沒有任何活動歸到這個名字）" in body and "時間主要去了 uav（5 天）" in body


def test_thresholds_come_from_config():
    settings = review_settings(_cfg(drift_max_days=2, drift_min_other_days=5))
    assert settings == {"enabled": True, "drift_max_days": 2, "drift_min_other_days": 5}
    cmp = compare_said_vs_done(active_days={"uav": 4, "thesis": 2}, priorities=["thesis"], settings=settings)
    assert cmp["drift"] == []                     # uav 4 < 5，不算「去了別處」


# ---- 回顧正文與寫入 ----


def test_body_says_counts_declared_and_digest_days(db):
    _active(db, "uav", [0, 1, 2, 3, 4]); _active(db, "thesis", [2])
    record_observation(title="09-08 工作誌", body="x", source_ref="daily_digest:2026-09-08", source="daily_digest", database=db, now=NOW)
    record_observation(title="09-08 · uav", body="x", source_ref="daily_digest:2026-09-08:uav", project_key="uav", source="daily_digest", database=db, now=NOW)
    record_observation(title="09-20 工作誌", body="x", source_ref="daily_digest:2026-09-20", source="daily_digest", database=db, now=NOW)
    assert digest_days_in(WEEK_START, WEEK_END, database=db) == 1      # 專案層與週外的不算
    add_note(kind="preference", body="優先：thesis", database=db, now=NOW)
    receipt = build_weekly_review(database=db, cfg=_cfg(), now=NOW)
    assert receipt["period_label"] == "2026-W37" and receipt["active_days"] == 5 and receipt["llm_used"] is False
    assert receipt["projects"] == [{"project": "uav", "days": 5}, {"project": "thesis", "days": 1}]
    assert receipt["aligned"] is False and receipt["drift"][0]["project"] == "thesis"
    text = receipt["text"]
    assert text.startswith("2026-W37（09-07～09-13）回顧：7 天裡 5 天有活動。")
    assert "依活躍天數：uav 5 天、thesis 1 天。" in text
    assert "你宣告的本期優先：thesis（1 天）——說的和做的不一致：時間主要去了 uav（5 天）。" in text
    assert text.endswith("每日工作誌 1/7 天。")


def test_body_without_declared_priorities_says_there_is_nothing_to_compare(db):
    _active(db, "uav", [0, 1])
    receipt = build_weekly_review(database=db, cfg=_cfg(), now=NOW)
    assert "你還沒宣告本期優先" in receipt["text"] and receipt["aligned"] is None


def _notes(db):
    with db.session_scope() as s:
        return [(n.source_ref, n.title, n.source, n.project_key) for n in s.query(SecretaryNote).order_by(SecretaryNote.id).all()]


def test_review_writes_one_note_per_week_and_reruns_do_not_duplicate(db):
    _active(db, "uav", [0, 1, 2])
    first = build_weekly_review(database=db, cfg=_cfg(), now=NOW)
    again = build_weekly_review(database=db, cfg=_cfg(), now=NOW + timedelta(days=1))
    assert first["notes_written"] == 1 and again["notes_written"] == 0
    assert _notes(db) == [("weekly_review:2026-W37", "2026-W37 回顧（09-07～09-13）", "weekly_review", None)]
    older = build_weekly_review(weeks_back=2, database=db, cfg=_cfg(), now=NOW)      # 那週沒活動：不寫
    assert older["observed_anything"] is False and older["notes_written"] == 0 and len(_notes(db)) == 1


def test_disabled_or_memory_off_writes_nothing(db):
    _active(db, "uav", [0, 1, 2])
    off = build_weekly_review(database=db, cfg=_cfg(enabled=False), now=NOW)
    assert off["status"] == "disabled" and off["notes_written"] == 0
    cfg = _cfg(); cfg.data["secretary_memory"]["enabled"] = False
    quiet = build_weekly_review(database=db, cfg=cfg, now=NOW)
    assert quiet["observed_anything"] is True and quiet["notes_written"] == 0 and _notes(db) == []


def test_module_never_reads_prompts_or_calls_an_llm():
    source = Path(wr.__file__).read_text(encoding="utf-8")
    for forbidden in ("prompt_text", "response_text", "llm_gateway", "requests.", "httpx", "subprocess"):
        assert forbidden not in source, forbidden


# ---- 提案引擎 ----


def _proposals(db, cfg):
    return build_action_proposals(database=db, cfg=cfg, now=NOW, extension_status={"extension": {"token_configured": False}})


def test_drift_signal_carries_the_said_vs_done_sentence_and_the_review_as_evidence(db):
    _active(db, "uav", [0, 1, 2, 3, 4]); _active(db, "thesis", [3])
    add_note(kind="preference", body="優先：thesis", database=db, now=NOW)
    build_weekly_review(database=db, cfg=_cfg(), now=NOW)
    signals, meta = collect_priority_drift_signals(database=db, cfg=_cfg(), now=NOW)
    assert meta["used"] is True and meta["period"] == "2026-W37" and meta["drift"] == 1 and meta["aligned"] is False
    sig = signals[0]
    assert sig["signal_type"] == "priority_drift" and sig["project_key"] == "thesis"
    assert sig["title"] == "你說「thesis」優先，上週它只有 1 天在動、uav 有 5 天"
    assert sig["score"] == pytest.approx(0.8) and sig["age_days"] == 3.0
    assert sig["evidence_extra"][0]["source_ref"] == "weekly_review:2026-W37"
    assert SUGGESTED_ACTIONS["priority_drift"] and why_now("priority_drift", 3.0)


def test_no_priorities_means_no_signal_and_an_honest_reason(db):
    _active(db, "uav", [0, 1, 2, 3, 4])
    assert collect_priority_drift_signals(database=db, cfg=_cfg(), now=NOW) == ([], {"used": True, "period": "2026-W37", "reason": "no_priorities", "drift": 0, "aligned": None})
    assert collect_priority_drift_signals(database=db, cfg=_cfg(enabled=False), now=NOW) == ([], {"used": False, "reason": "disabled"})


def test_engine_boosts_the_drift_card_and_drops_the_duplicate_neglect_card(db):
    # thesis：前一週活躍、近一週歸零 → ADR-017 會判「被冷落」；同時它是宣告的優先 → 只留 priority_drift
    _active(db, "uav", [0, 1, 2, 3, 4])
    for offset in (0, 1, 2, 3):
        _commit(db, "thesis", WEEK_START - timedelta(days=7 - offset))
    _schedule(db)
    add_note(kind="preference", body="優先：thesis", database=db, now=NOW)
    result = _proposals(db, _cfg())
    types = {(p["proposal_type"], p["project_key"]) for p in result["proposals"]}
    assert ("priority_drift", "thesis") in types and ("neglected_active_project", "thesis") not in types
    drift = next(p for p in result["proposals"] if p["proposal_type"] == "priority_drift")
    assert drift["priority_declared"] is True and drift["score"] == pytest.approx(1.0)   # 0.85 + 0.2 上限 1.0
    assert drift["why_now"] and "偏好：優先" in drift["suggested_action"]
    assert result["inputs"]["weekly_review"]["drift"] == 1


def test_drift_card_respects_mute_and_review_failure_is_isolated(db, monkeypatch):
    _active(db, "uav", [0, 1, 2, 3, 4])
    add_note(kind="preference", body="優先：thesis\n不要提醒 priority_drift", database=db, now=NOW)
    result = _proposals(db, _cfg())
    assert not [p for p in result["proposals"] if p["proposal_type"] == "priority_drift"]
    assert result["inputs"]["memory_muted"] >= 1

    def boom(**_kwargs):
        raise RuntimeError("review exploded")

    monkeypatch.setattr(wr, "collect_priority_drift_signals", boom)
    result = _proposals(db, _cfg())
    assert result["status"] == "proposal_only" and result["inputs"]["weekly_review"] == {"used": False, "reason": "error:RuntimeError"}


# ---- 排程、早晨包、桌面 ----


def test_weekly_review_is_a_read_only_schedulable_template_with_bounded_params():
    from core.agent_executor import ExecutionRejected
    from core.scheduled_tasks import SCHEDULABLE_TEMPLATES

    template = SCHEDULABLE_TEMPLATES["weekly_review"]
    assert template.risk_level == "L0_READ_ONLY"
    assert template.validate_params({"weeks_back": 2}) == {"weeks_back": 2}
    assert template.validate_params({}) == {"weeks_back": 1}
    with pytest.raises(ExecutionRejected):
        template.validate_params({"weeks_back": 9})
    with pytest.raises(ExecutionRejected):
        template.validate_params({"weeks_back": "上週"})
    assert "weekly_review_label" in SCHEDULABLE_TEMPLATES["morning_pack"].receipt_fields


def test_morning_pack_backfills_last_weeks_review_and_survives_its_failure(db, monkeypatch):
    from core.secretary_packs import build_morning_pack

    _active(db, "uav", [0, 1, 2])
    ok = lambda: {"status": "ok"}  # noqa: E731
    receipt = build_morning_pack(cfg=_cfg(), now=NOW, repo_sync=ok, status_draft=ok, handoffs=ok, database=db)
    assert receipt["weekly_review_label"] == "2026-W37" and receipt["weekly_review_notes_written"] == 1
    assert receipt["errors"] == []
    second = build_morning_pack(cfg=_cfg(), now=NOW + timedelta(days=1), repo_sync=ok, status_draft=ok, handoffs=ok, database=db)
    assert second["weekly_review_notes_written"] == 0        # 同一週不重寫

    def boom(**_kwargs):
        raise RuntimeError("review exploded")

    monkeypatch.setattr(wr, "build_weekly_review", boom)
    broken = build_morning_pack(cfg=_cfg(), now=NOW, repo_sync=ok, status_draft=ok, handoffs=ok, database=db)
    assert "weekly_review: RuntimeError" in broken["errors"] and "weekly_review_label" not in broken


def test_desk_picks_a_fresh_weekly_review_over_yesterdays_digest_but_not_a_stale_one(db):
    record_observation(title="09-15 工作誌", body="x", source_ref="daily_digest:2026-09-15", source="daily_digest", database=db, now=NOW - timedelta(hours=2))
    record_observation(title="2026-W37 回顧", body="說的 vs 做的", source_ref="weekly_review:2026-W37", source="weekly_review", database=db, now=NOW - timedelta(days=2))
    fresh = pick_memory(database=db, cfg=_cfg(), now=NOW)
    assert fresh["rule"] == "weekly_review" and fresh["note"]["title"] == "2026-W37 回顧"
    stale = pick_memory(database=db, cfg=_cfg(), now=NOW + timedelta(days=5))
    assert stale["rule"] == "daily_digest"


# ---- 驗收中心 A19 ----


def test_a19_reports_reviews_and_the_live_comparison(db):
    assert "A19" in ITEM_IDS
    cfg = _cfg()
    pending = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=["A19"])["items"][0]
    assert pending["status"] == "pending"
    _active(db, "uav", [0, 1, 2, 3, 4])
    add_note(kind="preference", body="優先：thesis", database=db, now=NOW)
    build_weekly_review(database=db, cfg=cfg, now=NOW)
    item = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=["A19"])["items"][0]
    assert item["status"] == "needs_human" and "1 項宣告優先「說了沒做」" in item["detail"]
    assert item["evidence"]["reviews_written"] == ["weekly_review:2026-W37"] and item["evidence"]["last_complete_week"]["label"] == "2026-W37"
    off = build_acceptance_report(database=db, cfg=_cfg(enabled=False), now=NOW, only=["A19"])["items"][0]
    assert off["status"] == "not_configured"
