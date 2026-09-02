"""小秘書每日包（早晨包／活躍專案 Handoff／預設排程）與「為什麼是現在」的契約。

- 兩個新 template 都是 L0 唯讀，走既有排程註冊表（L1/L2 仍不可排程）。
- 活躍專案 Handoff 只處理時窗內的專案、上限可控、單一失敗不拖垮其他專案。
- 早晨包三步各自 try/except，收據扁平且如實列出失敗步驟。
- 預設排程一鍵建立且 idempotent；開關關閉時 fail-closed。
- 每個提案都有一句「為什麼是現在」。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.agent_executor import RISK_L0
from core.models import AgentExecutionReceipt, Base
from core.proactive_secretary import SUGGESTED_ACTIONS, _signal_to_proposal, why_now
from core.scheduled_tasks import SCHEDULABLE_TEMPLATES, ScheduleRejected
from core.secretary_packs import (
    DEFAULT_PRESETS,
    build_active_handoffs,
    build_morning_pack,
    build_today_view,
    ensure_default_schedules,
    latest_pack_summary,
    pack_summary_line,
)
from core.server import app

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 2, 7, 30)


class DictConfig:
    def __init__(self, data):
        self.data = data

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


def _cfg(reports_dir: Path, executor=True, scheduled=True):
    return DictConfig({
        "exporters": {"reports_dir": str(reports_dir)},
        "proactive_secretary": {"executor": {"enabled": executor, "scheduled_tasks": {"enabled": scheduled}}},
    })


def _project(key, hours_ago):
    return {"project_key": key, "display_name": key, "status": "active",
            "last_activity_at": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")}


# ---- 註冊表 ----


def test_pack_templates_are_registered_as_l0():
    for template_id in ("morning_pack", "handoff_active_projects"):
        template = SCHEDULABLE_TEMPLATES[template_id]
        assert template.risk_level == RISK_L0
    assert "errors" in SCHEDULABLE_TEMPLATES["morning_pack"].receipt_fields
    with pytest.raises(ScheduleRejected):
        SCHEDULABLE_TEMPLATES["handoff_active_projects"].validate_params({"hours": 0}, None)
    with pytest.raises(ScheduleRejected):
        SCHEDULABLE_TEMPLATES["handoff_active_projects"].validate_params({"bogus": 1}, None)
    assert SCHEDULABLE_TEMPLATES["handoff_active_projects"].validate_params({"hours": "48"}, None) == {"hours": 48, "max_projects": 10}


# ---- 活躍專案 Handoff ----


def test_active_handoffs_respect_window_cap_and_isolate_failures(tmp_path):
    projects = [_project("fresh", 2), _project("today", 20), _project("old", 60), _project("broken", 1)]

    def build(key):
        if key == "broken":
            raise RuntimeError("no handoff data")
        return {"project": key}

    receipt = build_active_handoffs(
        hours=24, max_projects=2, cfg=_cfg(tmp_path), now=NOW,
        projects=projects, build=build, fmt=lambda data: f"# {data['project']}\n",
    )
    # 24 小時內有三個候選（fresh、today、broken）；上限 2 → 最新的 broken 與 fresh
    assert receipt["projects_considered"] == 3
    assert receipt["handoffs_written"] == 1 and receipt["projects"] == ["fresh"]
    assert receipt["errors"] == ["broken: RuntimeError"]
    written = sorted(p.name for p in (tmp_path / "handoffs").glob("*.md"))
    assert written == ["Handoff_fresh_20260902.md"]
    assert (tmp_path / "handoffs" / "Handoff_fresh_20260902.md").read_text(encoding="utf-8") == "# fresh\n"


# ---- 早晨包 ----


def test_morning_pack_is_flat_and_records_failed_steps(tmp_path):
    receipt = build_morning_pack(
        cfg=_cfg(tmp_path), now=NOW,
        repo_sync=lambda: {"repos_scanned": 12, "needs_pull": 3, "needs_push": 1, "diverged": 0},
        status_draft=lambda: (_ for _ in ()).throw(OSError("repo unreadable")),
        handoffs=lambda: {"handoffs_written": 4},
    )
    assert receipt["repos_scanned"] == 12 and receipt["needs_pull"] == 3 and receipt["handoffs_written"] == 4
    assert receipt["stale_status"] is None
    assert receipt["errors"] == ["status_snapshot_draft: OSError"]
    # 全部值都是純量或短清單，能塞進 500 字的 output_summary
    assert len(json.dumps({k: receipt[k] for k in SCHEDULABLE_TEMPLATES["morning_pack"].receipt_fields})) < 500
    assert pack_summary_line(receipt) == "早晨包：repo 需 pull 3、需 push 1、Handoff 4 份、1 步失敗"
    assert pack_summary_line(None) is None


def test_latest_pack_summary_reads_only_fresh_succeeded_receipt():
    database = TempDatabase()
    with database.session_scope() as session:
        session.add(AgentExecutionReceipt(
            proposal_id="scheduled_task:1", template_id="morning_pack", risk_level=RISK_L0,
            action_call="morning_pack", status="failed", approved_via="schedule",
            requested_at=NOW - timedelta(hours=1), finished_at=NOW - timedelta(hours=1),
            output_summary=json.dumps({"needs_pull": 99}),
        ))
        session.add(AgentExecutionReceipt(
            proposal_id="scheduled_task:1", template_id="morning_pack", risk_level=RISK_L0,
            action_call="morning_pack", status="succeeded", approved_via="schedule",
            requested_at=NOW - timedelta(hours=2), finished_at=NOW - timedelta(hours=2),
            output_summary=json.dumps({"needs_pull": 2, "needs_push": 0, "stale_status": 1, "handoffs_written": 3, "errors": []}),
        ))
    summary = latest_pack_summary(database=database, now=NOW)
    assert summary["needs_pull"] == 2 and summary["approved_via"] == "schedule"
    assert pack_summary_line(summary) == "早晨包：repo 需 pull 2、需 push 0、STATUS 過期 1、Handoff 3 份"
    assert latest_pack_summary(database=database, now=NOW + timedelta(days=3)) is None


# ---- 預設排程 ----


def test_default_schedules_are_created_once_and_fail_closed(tmp_path):
    database = TempDatabase()
    cfg = _cfg(tmp_path)
    first = ensure_default_schedules(database=database, cfg=cfg, now=NOW)
    assert [task["template_id"] for task in first["created"]] == [p["template_id"] for p in DEFAULT_PRESETS]
    assert first["already_present"] == []
    again = ensure_default_schedules(database=database, cfg=cfg, now=NOW)
    assert again["created"] == [] and sorted(again["already_present"]) == sorted(p["template_id"] for p in DEFAULT_PRESETS)
    with pytest.raises(ScheduleRejected):
        ensure_default_schedules(database=TempDatabase(), cfg=_cfg(tmp_path, scheduled=False), now=NOW)


def test_today_view_is_read_only_summary(tmp_path):
    database = TempDatabase()
    view = build_today_view(
        database=database, cfg=_cfg(tmp_path), now=NOW,
        projects=[{**_project("alpha", 1), "last_action_summary": "Git: fix", "open_loops_count": 2}],
    )
    assert view["resume"]["project_key"] == "alpha" and view["resume"]["open_loops_count"] == 2
    assert view["pack"] is None and view["pack_line"] is None
    assert view["schedules"]["scheduled_tasks_enabled"] is True and view["schedules"]["all_present"] is False


# ---- 為什麼是現在 ----


def test_every_proposal_type_explains_why_now():
    for signal_type in list(SUGGESTED_ACTIONS) + ["repo_diverged"]:
        assert why_now(signal_type, 3.0), signal_type
    proposal = _signal_to_proposal({
        "signal_type": "unfinished_recent", "project_key": "alpha", "subject_ref": "project_states:1",
        "title": "alpha 尚未收尾", "detail": "", "reasons": ["最近有活動"], "score": 0.66,
        "evidence_ref": "project_states:1", "observed_at": None, "age_days": 0.75, "open_loop_refs": [],
    }, NOW)
    assert proposal["why_now"] == "18 小時前還在動，脈絡還新鮮，現在收尾最省力"


# ---- API ----


def test_today_and_presets_endpoints(monkeypatch):
    import core.secretary_packs as packs

    monkeypatch.setattr(packs, "build_today_view", lambda: {"resume": None, "pack_line": None, "schedules": {}})
    client = TestClient(app)
    res = client.get("/api/v1/secretary/today", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and "schedules" in res.json()
    # 預設排程是 mutation：沒有 execution token 一律 401
    res = client.post("/api/v1/secretary/scheduled-tasks/presets", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 401


def test_sync_snapshot_endpoint_is_honest_when_missing(monkeypatch):
    import core.repo_sync_report as report

    monkeypatch.setattr(report, "load_snapshot", lambda cfg=None: None)
    client = TestClient(app)
    res = client.get("/api/v1/repos/sync-snapshot", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and res.json()["available"] is False
    monkeypatch.setattr(report, "load_snapshot", lambda cfg=None: {
        "generated_at": "2026-09-02T07:30:00", "repositories": [{"repo_id": "a" * 16, "name": "alpha", "path": "/x/alpha",
        "branch": "main", "ahead": 0, "behind": 2, "sync_state": "behind", "clean": True, "last_fetch_at": None, "error": None,
        "dirty_files": 0}], "summary": {"behind": 1},
    })
    res = client.get("/api/v1/repos/sync-snapshot", headers={"Origin": _LOCAL_ORIGIN})
    body = res.json()
    assert body["available"] is True and body["repositories"][0]["sync_state"] == "behind"
    assert "dirty_files" not in body["repositories"][0]
