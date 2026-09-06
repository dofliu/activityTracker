"""秘書桌面（ADR-019）：01 成為真正的首頁——卡片由秘書決定該顯示什麼。

- 焦點永遠是提案引擎排序後的第一張（引擎已含 mute／snooze／習慣加權／宣告優先）；沒有提案就如實說沒有。
- 「記得」依固定順序挑一則：焦點專案的決定／筆記 → 最近一天的工作誌（日層、未過期）→ 釘選 → 最近記下的；挑不到就給提示。
- 每一節各自隔離失敗：今日視圖壞了焦點照出，收據 sections 如實寫 error。
- 詳情計數來自既有唯讀資料；個人檔案一行來自 ADR-018。
- 不呼叫 LLM、不寫任何資料；API 唯讀；驗收中心 A18 只回報機器能看到的。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import secretary_home as sh
from core.acceptance import ITEM_IDS, build_acceptance_report
from core.models import Base, SecretaryNote
from core.secretary_home import MEMORY_PICK_RULES, NO_MEMORY_HINT, build_home, pick_memory
from core.secretary_memory import add_note, record_observation
from core.server import app

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 15, 10, 0)


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
    data = {"proactive_secretary": {"enabled": True}, "secretary_memory": {"enabled": True, "observation_ttl_days": 14},
            "exporters": {"reports_dir": "/nonexistent"}}
    data.update(extra)
    return DictConfig(data)


def _proposal(project, title, score, ptype="repo_needs_pull"):
    return {"proposal_id": f"{ptype}:{project}", "proposal_type": ptype, "project_key": project, "title": title,
            "detail": "", "reason": "落後", "why_now": "遠端有新 commit", "score": score, "priority": "medium",
            "evidence_refs": [], "url": None, "execution_available": False}


def _today(**over):
    base = {"resume": {"project_key": "alpha", "display_name": "Alpha", "last_activity_at": "2026-09-15T08:00:00",
                       "last_action_summary": "修 CI", "open_loops_count": 2},
            "active_project_count": 3, "pack_line": "早晨包：repo 需 pull 2", "pack": {},
            "memory": {"enabled": True, "counts": {"user_note": 2, "preference": 1, "decision": 1, "observation": 4}, "total": 8},
            "calendar": {"enabled": True, "count": 2, "line": "今天 2 場行程，下一場 14:00 專案會議", "claim_boundary": "x"},
            "schedules": {}}
    base.update(over)
    return base


def _digest(db, day: str, *, when: datetime, project=None):
    record_observation(title=f"{day} 工作誌" + (f" · {project}" if project else ""), body="3 commit",
                       source_ref=f"daily_digest:{day}" + (f":{project}" if project else ""), project_key=project,
                       source="daily_digest", database=db, now=when)


# ---- 焦點 ----


def test_focus_is_the_engines_first_proposal_and_counts_the_rest(db):
    proposals = [_proposal("thesis", "thesis 落後", 0.9), _proposal("uav", "uav 落後", 0.7), _proposal("old", "old 落後", 0.5)]
    home = build_home(database=db, cfg=_cfg(), now=NOW, proposals=proposals, today=_today())
    assert home["focus"]["proposal"]["project_key"] == "thesis"
    assert home["focus"]["total"] == 3 and home["focus"]["remaining"] == 2
    assert "關於你的工作" in home["focus"]["basis"] and home["focus"]["skipped_system"] == []
    assert home["sections"]["focus"] == "ok"


def test_no_proposals_means_no_focus_not_an_error(db):
    home = build_home(database=db, cfg=_cfg(), now=NOW, proposals=[], today=_today())
    assert home["focus"] == {"proposal": None, "total": 0, "remaining": 0, "skipped_system": [], "basis": home["focus"]["basis"]}
    assert home["sections"]["focus"] == "ok"


def test_focus_is_about_your_work_not_the_tools_own_setup(db):
    """extension 沒 heartbeat（永遠 1.0、HIGH）不該永久佔住首頁焦點；它留在清單裡，沒有別的可看時才上來。"""
    system = _proposal("OmniContext", "驗證 Browser Extension 即時連線", 1.0, ptype="verify_extension_heartbeat")
    routine = _proposal("OmniContext", "你近一週有 5 天在工作，但秘書還沒有每日排程", 0.6, ptype="no_daily_routine")
    work = _proposal("oldPaper", "oldPaper 前一週活躍 4 天，近一週完全沒動", 0.7, ptype="neglected_active_project")
    home = build_home(database=db, cfg=_cfg(), now=NOW, proposals=[system, work, routine], today=_today())
    assert home["focus"]["proposal"]["project_key"] == "oldPaper"
    assert home["focus"]["skipped_system"] == ["verify_extension_heartbeat"] and home["focus"]["total"] == 3
    only_system = build_home(database=db, cfg=_cfg(), now=NOW, proposals=[system, routine], today=_today())
    assert only_system["focus"]["proposal"]["proposal_type"] == "verify_extension_heartbeat"
    assert only_system["focus"]["skipped_system"] == []


def test_focus_comes_from_the_real_engine_when_not_injected(db, monkeypatch):
    import core.proactive_secretary as ps

    monkeypatch.setattr(ps, "build_action_proposals", lambda **kw: {"proposals": [_proposal("uav", "uav 落後", 0.7)]})
    home = build_home(database=db, cfg=_cfg(), now=NOW, today=_today())
    assert home["focus"]["proposal"]["title"] == "uav 落後" and home["details"]["proposals"] == 1


# ---- 記得 ----


def test_memory_pick_prefers_the_focus_projects_decision(db):
    _digest(db, "2026-09-14", when=NOW - timedelta(hours=3))
    add_note(kind="decision", body="alpha 等 v2 再 merge", project_key="alpha", database=db, now=NOW - timedelta(days=2))
    add_note(kind="user_note", body="週五 demo", database=db, now=NOW - timedelta(hours=1))
    pick = pick_memory(database=db, cfg=_cfg(), now=NOW, focus_project_key="ALPHA")   # 不分大小寫
    assert pick["rule"] == "focus_project" and pick["note"]["body"] == "alpha 等 v2 再 merge"
    assert pick["why_this"] == MEMORY_PICK_RULES["focus_project"]


def test_memory_pick_falls_back_to_the_latest_day_level_digest(db):
    _digest(db, "2026-09-13", when=NOW - timedelta(days=1, hours=3))
    _digest(db, "2026-09-14", when=NOW - timedelta(hours=3))
    _digest(db, "2026-09-14", when=NOW - timedelta(hours=2), project="uav")      # 專案層不算「一天的工作誌」
    record_observation(title="早晨包", body="掃描 12 個 repo", source_ref="pack:2026-09-15", source="morning_pack", database=db, now=NOW)
    add_note(kind="user_note", body="舊筆記", database=db, now=NOW - timedelta(days=5))
    pick = pick_memory(database=db, cfg=_cfg(), now=NOW, focus_project_key="nobody")
    assert pick["rule"] == "daily_digest" and pick["note"]["title"] == "2026-09-14 工作誌"


def test_expired_digest_is_skipped_then_pinned_then_recent(db):
    _digest(db, "2026-08-10", when=NOW - timedelta(days=36))                    # 超過 TTL
    add_note(kind="user_note", body="最新", database=db, now=NOW - timedelta(hours=1))
    add_note(kind="user_note", body="釘選的", pinned=True, database=db, now=NOW - timedelta(days=3))
    pinned = pick_memory(database=db, cfg=_cfg(), now=NOW)
    assert pinned["rule"] == "pinned" and pinned["note"]["body"] == "釘選的"
    with db.session_scope() as s:
        for row in s.query(SecretaryNote).filter(SecretaryNote.pinned.is_(True)).all():
            row.pinned = False
    recent = pick_memory(database=db, cfg=_cfg(), now=NOW)
    assert recent["rule"] == "recent" and recent["note"]["body"] == "最新"


def test_nothing_to_pick_is_said_honestly(db):
    add_note(kind="preference", body="語氣：簡潔", database=db, now=NOW)        # 偏好不是「記得」的候選
    pick = pick_memory(database=db, cfg=_cfg(), now=NOW)
    assert pick == {"note": None, "rule": None, "why_this": None, "hint": NO_MEMORY_HINT}


# ---- 組合與隔離 ----


def test_home_composes_resume_calendar_profile_and_detail_counts(db):
    add_note(kind="preference", body="優先：alpha\n語氣：直接", database=db, now=NOW)
    home = build_home(database=db, cfg=_cfg(), now=NOW, proposals=[_proposal("alpha", "alpha 落後", 0.8)], today=_today())
    assert home["resume"]["display_name"] == "Alpha" and home["calendar"]["line"].startswith("今天 2 場行程")
    assert home["pack_line"] == "早晨包：repo 需 pull 2"
    assert home["profile_line"] == "本期優先：alpha／語氣：直接"
    assert home["details"] == {"proposals": 1, "notes": 8, "notes_counts": {"user_note": 2, "preference": 1, "decision": 1, "observation": 4},
                               "active_projects": 3, "open_loops": 2, "calendar_events": 2}
    assert home["sections"] == {"focus": "ok", "today": "ok", "memory": "ok", "profile": "ok"}
    assert "不呼叫 LLM" in home["claim_boundary"]


def test_a_broken_today_view_does_not_take_the_focus_down(db, monkeypatch):
    import core.secretary_packs as packs

    def boom(**_kwargs):
        raise RuntimeError("today exploded")

    monkeypatch.setattr(packs, "build_today_view", boom)
    home = build_home(database=db, cfg=_cfg(), now=NOW, proposals=[_proposal("uav", "uav 落後", 0.7)])
    assert home["focus"]["proposal"]["project_key"] == "uav"
    assert home["sections"]["today"] == "error:RuntimeError" and home["sections"]["focus"] == "ok"
    assert home["resume"] == {} and home["details"]["active_projects"] == 0 and home["details"]["notes"] == 0


def test_a_broken_memory_pick_leaves_the_hint(db, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("memory exploded")

    monkeypatch.setattr(sh, "pick_memory", boom)
    home = build_home(database=db, cfg=_cfg(), now=NOW, proposals=[], today=_today())
    assert home["sections"]["memory"] == "error:RuntimeError"
    assert home["memory_pick"]["note"] is None and home["memory_pick"]["hint"] == NO_MEMORY_HINT


def test_home_never_writes_and_never_calls_an_llm(db):
    add_note(kind="user_note", body="x", database=db, now=NOW)
    _digest(db, "2026-09-14", when=NOW)
    with db.session_scope() as s:
        before = s.query(SecretaryNote).count()
    build_home(database=db, cfg=_cfg(), now=NOW, proposals=[_proposal("uav", "uav", 0.5)], today=_today())
    with db.session_scope() as s:
        assert s.query(SecretaryNote).count() == before
    source = Path(sh.__file__).read_text(encoding="utf-8")
    for forbidden in ("llm_gateway", "requests.", "httpx", "subprocess", "prompt_text", "session.add", "session.delete"):
        assert forbidden not in source, forbidden


# ---- API ----


def test_home_endpoint_is_read_only(monkeypatch):
    monkeypatch.setattr("core.secretary_home.build_home", lambda: {"focus": {"proposal": None, "total": 0, "remaining": 0}, "sections": {}, "claim_boundary": "x"})
    client = TestClient(app)
    res = client.get("/api/v1/secretary/home", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and res.json()["focus"]["total"] == 0
    assert client.post("/api/v1/secretary/home", json={}, headers={"Origin": _LOCAL_ORIGIN}).status_code == 405


# ---- 驗收中心 A18 ----


def _a18(db, cfg, monkeypatch, home):
    monkeypatch.setattr("core.secretary_home.build_home", lambda **kw: home)
    report = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=["A18"])
    return report["items"][0]


def test_a18_is_registered_and_reports_only_what_the_machine_can_see(db, monkeypatch):
    assert "A18" in ITEM_IDS
    empty = {"focus": {"proposal": None}, "memory_pick": {"note": None, "rule": None}, "sections": {"focus": "ok", "today": "ok", "memory": "ok", "profile": "ok"}, "details": {}}
    assert _a18(db, _cfg(), monkeypatch, empty)["status"] == "pending"
    broken = {**empty, "sections": {"focus": "error:RuntimeError", "today": "ok", "memory": "ok", "profile": "ok"}}
    item = _a18(db, _cfg(), monkeypatch, broken)
    assert item["status"] == "partial" and "focus=error:RuntimeError" in item["detail"]
    full = {"focus": {"proposal": _proposal("uav", "uav 落後", 0.7)}, "memory_pick": {"note": {"id": 1}, "rule": "daily_digest", "why_this": "最近一天的工作誌"},
            "sections": {"focus": "ok", "today": "ok", "memory": "ok", "profile": "ok"}, "details": {"proposals": 1}}
    item = _a18(db, _cfg(), monkeypatch, full)
    assert item["status"] == "needs_human" and "uav 落後" in item["detail"] and item["evidence"]["memory_pick_rule"] == "daily_digest"
