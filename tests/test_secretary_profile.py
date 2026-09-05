"""宣告式個人檔案（ADR-018）：你自己說的，不是推測的。

- 只認偏好筆記裡的「優先：」「語氣：」兩種明確宣告；其餘句子原樣忽略、不推斷。
- 中英別名都認、專案名去重、未知語氣忽略且如實記在 ignored。
- 優先只影響排序：所有訊號（含 ADR-017 的被冷落訊號）加分、上限 1.0、附理由；加分刻意大於習慣加權。
- 語氣只影響問候措辭：簡潔不講鼓勵語、直接一句話、溫暖沿用原池；三種語氣下的數字完全相同。
- 對話脈絡多一行「個人檔案（你宣告的）」並在收據列出 profile 區段；沒宣告就沒這一行。
- 引擎與問候讀個人檔案失敗都不得拖垮原本的結果；API 唯讀；模組不讀 prompt、不呼叫 LLM。
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import secretary_profile as sp
from core.models import Base, GitActivityEvent, SecretaryScheduledTask
from core.proactive_secretary import build_action_proposals
from core.secretary_greeting import build_greeting, compose_greeting, plain_text
from core.secretary_memory import add_note, memory_context
from core.secretary_profile import (
    DEFAULT_PRIORITY_BOOST,
    apply_priority_boost,
    load_profile,
    parse_profile_directives,
    priority_boost_value,
    profile_summary_line,
)
from core.server import app

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 15, 10, 0)  # 週二上午


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
    def __init__(self, path: Path | None = None):
        self.engine = create_engine(f"sqlite:///{path}" if path else "sqlite:///:memory:")
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


def _cfg(**extra):
    data = {"proactive_secretary": {"enabled": True, "max_proposals": 12},
            "secretary_memory": {"enabled": True}, "exporters": {"reports_dir": "/nonexistent"}}
    data["proactive_secretary"].update(extra)
    return DictConfig(data)


def _pref(db, body, *, when=NOW):
    return add_note(kind="preference", body=body, database=db, now=when)


# ---- 解析 ----


def test_parse_recognises_zh_and_en_aliases_dedupes_and_ignores_the_rest():
    profile = parse_profile_directives([
        "回答用繁體中文",                       # 一般偏好：不是指令，原樣忽略
        "優先：uavMonitor、論文",
        "priority: 論文, OmniContext ; UAVMONITOR",   # 英文別名、不同分隔、大小寫視為同一專案
        "本期優先：「BladeDamage」。",
        "語氣：簡潔",
    ])
    assert profile["priorities"] == ["uavMonitor", "論文", "OmniContext", "BladeDamage"]
    assert profile["tone"] == "brief" and profile["tone_label"] == "簡潔" and profile["tone_declared"] is True
    assert profile["ignored"] == []


@pytest.mark.parametrize("text, tone", [
    ("tone: direct", "direct"), ("語氣：直接", "direct"), ("語氣：乾脆", "direct"),
    ("語氣：溫暖", "warm"), ("tone:warm", "warm"), ("語氣：精簡", "brief"), ("tone: short", "brief"),
])
def test_tone_aliases_resolve(text, tone):
    profile = parse_profile_directives([text])
    assert profile["tone"] == tone and profile["tone_declared"] is True


def test_unknown_tone_is_ignored_honestly_and_later_declaration_wins():
    profile = parse_profile_directives(["語氣：搞笑", "語氣：直接", "語氣：簡潔"])
    assert profile["tone"] == "brief" and profile["tone_declared"] is True
    assert profile["ignored"] == ["tone:搞笑"]
    nothing = parse_profile_directives(["mute:legacy-app", "不要提醒 legacy-app"])
    assert nothing == {"priorities": [], "tone": "warm", "tone_label": "溫暖", "tone_declared": False, "ignored": []}


def test_priorities_are_capped_so_a_list_of_everything_is_not_a_priority():
    profile = parse_profile_directives(["優先：" + "、".join(f"p{i}" for i in range(20))])
    assert len(profile["priorities"]) == sp.MAX_PRIORITIES


# ---- 讀取 ----


def test_load_profile_reads_only_preference_notes_newest_tone_wins(db):
    add_note(kind="user_note", body="優先：不該算的（這是筆記不是偏好）", database=db, now=NOW)
    add_note(kind="decision", body="語氣：直接", database=db, now=NOW)
    _pref(db, "語氣：直接", when=NOW - timedelta(days=2))
    _pref(db, "優先：uavMonitor", when=NOW - timedelta(days=1))
    _pref(db, "語氣：簡潔", when=NOW)
    profile = load_profile(database=db)
    assert profile["priorities"] == ["uavMonitor"] and profile["tone"] == "brief"
    assert profile["declared"] is True and profile["source"] == "secretary_notes.kind=preference"
    assert "不從活動或對話推斷" in profile["claim_boundary"] and "偏好：優先：" in profile["how_to_set"]


def test_load_profile_without_declarations_is_default_and_says_so(db):
    _pref(db, "回答用繁體中文")
    profile = load_profile(database=db)
    assert profile["declared"] is False and profile["priorities"] == [] and profile["tone"] == "warm"
    assert profile_summary_line(profile) == ""


def test_summary_line_only_mentions_what_was_declared():
    assert profile_summary_line({"priorities": ["uav", "論文"], "tone": "warm", "tone_declared": False}) == "本期優先：uav、論文"
    assert profile_summary_line({"priorities": [], "tone": "direct", "tone_declared": True}) == "語氣：直接"
    assert profile_summary_line({"priorities": ["uav"], "tone": "brief", "tone_declared": True}) == "本期優先：uav／語氣：簡潔"


# ---- 優先加分 ----


def test_priority_boost_value_defaults_and_clamps():
    assert priority_boost_value(DictConfig()) == DEFAULT_PRIORITY_BOOST == 0.2
    assert priority_boost_value(DictConfig({"proactive_secretary": {"profile": {"priority_boost": 0.9}}})) == 0.5
    assert priority_boost_value(DictConfig({"proactive_secretary": {"profile": {"priority_boost": -1}}})) == 0.0
    assert priority_boost_value(DictConfig({"proactive_secretary": {"profile": {"priority_boost": "abc"}}})) == DEFAULT_PRIORITY_BOOST
    assert DEFAULT_PRIORITY_BOOST > 0.15   # 刻意大於 ADR-017 的 habit_boost 預設：你說的優先勝過推出來的主線


def test_apply_priority_boost_boosts_every_signal_of_the_project_including_neglect():
    signals = [
        {"signal_type": "repo_needs_pull", "project_key": "uavMonitor", "score": 0.55, "reasons": ["落後"]},
        {"signal_type": "neglected_active_project", "project_key": "uavmonitor", "score": 0.6, "reasons": []},
        {"signal_type": "aging_pr", "project_key": "uavMonitor", "score": 0.95, "reasons": []},
        {"signal_type": "repo_needs_pull", "project_key": "old", "score": 0.55, "reasons": ["落後"]},
        {"signal_type": "verify_extension_heartbeat", "project_key": None, "score": 0.4, "reasons": []},
    ]
    assert apply_priority_boost(signals, ["UAVMonitor"], boost=0.2) == 3
    assert signals[0]["score"] == pytest.approx(0.75) and signals[0]["priority_declared"] is True
    assert signals[0]["reasons"][-1] == "你把這個專案標為本期優先"
    assert signals[1]["score"] == pytest.approx(0.8)            # 被冷落的優先專案更該浮上來（與習慣加權不同）
    assert signals[2]["score"] == 1.0                            # 不超過 1.0
    assert signals[3]["score"] == pytest.approx(0.55) and "priority_declared" not in signals[3]
    assert signals[4]["score"] == pytest.approx(0.4)


def test_apply_priority_boost_is_a_noop_without_priorities_or_boost():
    signals = [{"signal_type": "repo_needs_pull", "project_key": "uav", "score": 0.5, "reasons": []}]
    assert apply_priority_boost(signals, [], boost=0.2) == 0
    assert apply_priority_boost(signals, ["uav"], boost=0) == 0
    assert signals[0] == {"signal_type": "repo_needs_pull", "project_key": "uav", "score": 0.5, "reasons": []}


# ---- 進提案引擎 ----

_COMMIT_SEQ = itertools.count(1)


def _active_on(db, project: str, offsets: list[int]):
    with db.session_scope() as s:
        for offset in offsets:
            when = datetime.combine(NOW.date() - timedelta(days=offset), datetime.min.time()) + timedelta(hours=10)
            s.add(GitActivityEvent(timestamp=when, repo_name=project, repo_path=f"/r/{project}",
                                   commit_hash=f"{project}-{next(_COMMIT_SEQ):06d}", message="m"))
        # 建一個每日排程，讓 no_daily_routine 不出現，專心看排序
        s.add(SecretaryScheduledTask(template_id="morning_pack", schedule_kind="daily", run_time="07:30", enabled=True))


def _fake_repo_signals(*, cfg=None, now=None):
    base = {"subject_ref": "repo:x", "observed_at": now, "url": None, "age_days": 0.1, "open_loop_refs": [],
            "detail": "", "reasons": ["落後"], "score": 0.55}
    return [
        {**base, "signal_type": "repo_needs_pull", "project_key": "uav", "evidence_ref": "repo_sync_snapshot:1",
         "subject_ref": "repo:1", "title": "uav 落後"},
        {**base, "signal_type": "repo_needs_pull", "project_key": "thesis", "evidence_ref": "repo_sync_snapshot:2",
         "subject_ref": "repo:2", "title": "thesis 落後"},
    ], {"used": True}


def _proposals(db, cfg):
    return build_action_proposals(database=db, cfg=cfg, now=NOW,
                                  extension_status={"extension": {"token_configured": False}})


def test_declared_priority_outranks_the_inferred_main_line(db, monkeypatch):
    """uav 近一週天天在動（習慣加權 +0.15）；你卻宣告 thesis 是本期優先（+0.2）→ thesis 排前面。"""
    import core.repo_sync_report as rsr

    monkeypatch.setattr(rsr, "collect_repo_sync_signals", _fake_repo_signals)
    _active_on(db, "uav", [1, 2, 3, 4, 5])
    _pref(db, "優先：thesis")
    result = _proposals(db, _cfg())
    pulls = [p for p in result["proposals"] if p["proposal_type"] == "repo_needs_pull"]
    assert [p["project_key"] for p in pulls] == ["thesis", "uav"]
    assert pulls[0]["priority_declared"] is True and pulls[0]["score"] == pytest.approx(0.75)
    assert "本期優先" in pulls[0]["reason"]
    assert pulls[1]["habit_boosted"] is True and "priority_declared" not in pulls[1]
    assert result["inputs"]["profile"] == {"declared": True, "priorities": ["thesis"], "tone": "warm", "priority_boosted": 1}
    assert result["inputs"]["patterns"]["habit_boosted"] == 1


def test_without_a_declaration_the_engine_reports_it_and_changes_nothing(db, monkeypatch):
    import core.repo_sync_report as rsr

    monkeypatch.setattr(rsr, "collect_repo_sync_signals", _fake_repo_signals)
    result = _proposals(db, _cfg())
    pulls = [p for p in result["proposals"] if p["proposal_type"] == "repo_needs_pull"]
    assert all("priority_declared" not in p for p in pulls) and all(p["score"] == pytest.approx(0.55) for p in pulls)
    assert result["inputs"]["profile"] == {"declared": False, "priorities": [], "tone": "warm", "priority_boosted": 0}


def test_profile_failure_does_not_break_the_proposal_list(db, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("profile exploded")

    monkeypatch.setattr(sp, "load_profile", boom)
    result = _proposals(db, _cfg())
    assert result["status"] == "proposal_only"
    assert result["inputs"]["profile"] == {"declared": False, "reason": "error:RuntimeError"}


# ---- 對話脈絡 ----


def test_memory_context_states_the_declared_profile_in_one_line(db):
    _pref(db, "回答用繁體中文")
    _pref(db, "優先：uavMonitor、論文")
    _pref(db, "語氣：直接")
    out = memory_context(database=db, cfg=_cfg(), now=NOW, today={}, proposals=[])
    text, receipt = out["text"], out["receipt"]
    assert "個人檔案（你宣告的）：本期優先：uavMonitor、論文／語氣：直接" in text
    assert receipt["sections"] == ["profile", "notes"]
    assert text.index("個人檔案（你宣告的）") < text.index("偏好與決定：")   # 先講結論，再列來源筆記
    assert "優先：uavMonitor、論文" in text                                      # 來源筆記仍在，不是第二套資料


def test_memory_context_without_declarations_has_no_profile_line(db):
    _pref(db, "回答用繁體中文")
    out = memory_context(database=db, cfg=_cfg(), now=NOW, today={}, proposals=[])
    assert "個人檔案" not in out["text"] and out["receipt"]["sections"] == ["notes"]


# ---- 問候語氣 ----

_STATS = {"window": "today", "commits": 3, "prs_opened": 1, "prs_merged": 0, "ai_rounds": 5, "files_touched": 2,
          "projects_touched": 2, "open_loops_resolved": 0, "elapsed_hours": 2.5, "first_activity_at": "2026-09-15T08:00:00",
          "recent_summaries": [], "sources": {}}


def test_tone_changes_only_the_encouragement_never_the_facts():
    warm = compose_greeting(dict(_STATS), now=NOW, name="Dof", tone="warm")
    brief = compose_greeting(dict(_STATS), now=NOW, name="Dof", tone="brief")
    direct = compose_greeting(dict(_STATS), now=NOW, name="Dof", tone="direct")
    for key in ("headline", "lead", "achievements", "stats", "schedule_line", "claim_boundary"):
        assert warm[key] == brief[key] == direct[key]
    assert warm["tone"] == "warm" and warm["encouragement"] and warm["encouragement_pool"] != "direct"
    assert brief["tone"] == "brief" and brief["encouragement"] == "" and brief["encouragement_pool"] == "brief_omitted"
    assert direct["tone"] == "direct" and direct["encouragement"] in sp_direct_pool() and direct["encouragement_pool"] == "direct"
    assert not plain_text(brief).endswith(" ") and plain_text(brief) == plain_text(warm).replace(" " + warm["encouragement"], "")


def sp_direct_pool():
    from core.secretary_greeting import ENCOURAGEMENT_POOLS

    return ENCOURAGEMENT_POOLS["direct"]


def test_unknown_tone_falls_back_to_warm():
    greeting = compose_greeting(dict(_STATS), now=NOW, name="", tone="sarcastic")
    assert greeting["tone"] == "warm" and greeting["encouragement"]


def test_build_greeting_reads_the_tone_from_preference_notes(db):
    _pref(db, "語氣：簡潔")
    cfg = DictConfig({"proactive_secretary": {"greeting": {"display_name": "Dof", "llm": {"enabled": False}}}})
    greeting = build_greeting(window="today", now=NOW, database=db, cfg=cfg, use_llm=False)
    assert greeting["tone"] == "brief" and greeting["encouragement"] == ""
    assert greeting["headline"].startswith("Dof，")


def test_build_greeting_survives_profile_failure(db, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("profile exploded")

    monkeypatch.setattr(sp, "load_profile", boom)
    cfg = DictConfig({"proactive_secretary": {"greeting": {"display_name": "", "llm": {"enabled": False}}}})
    greeting = build_greeting(window="today", now=NOW, database=db, cfg=cfg, use_llm=False)
    assert greeting["tone"] == "warm" and greeting["source"] == "rules"


# ---- API ----


def test_profile_endpoint_is_read_only(monkeypatch, tmp_path):
    db = TempDatabase(tmp_path / "profile.db")
    _pref(db, "優先：uavMonitor")
    monkeypatch.setattr("core.secretary_profile.get_db", lambda: db)
    client = TestClient(app)
    res = client.get("/api/v1/secretary/profile", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200
    body = res.json()
    assert body["declared"] is True and body["priorities"] == ["uavMonitor"] and body["tone"] == "warm"
    assert body["source"] == "secretary_notes.kind=preference" and "how_to_set" in body and "claim_boundary" in body
    assert client.post("/api/v1/secretary/profile", json={}, headers={"Origin": _LOCAL_ORIGIN}).status_code == 405


# ---- 邊界 ----


def test_module_never_infers_from_prompts_or_calls_an_llm():
    source = Path(sp.__file__).read_text(encoding="utf-8")
    for forbidden in ("prompt_text", "response_text", "llm_gateway", "AIPromptEvent", "requests.", "httpx"):
        assert forbidden not in source, forbidden
