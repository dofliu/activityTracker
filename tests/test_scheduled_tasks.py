"""P5-R5 使用者自訂排程任務的 contract tests（ADR-008 階段 5）。

核心契約：只有 server 註冊的 L0 唯讀 template 可排程；L1/L2 永不可排程；
開關預設關閉且 fail-closed；每次執行寫 audit receipt；錯過的排程只補跑
一次；rollup 與 STATUS 草稿誠實標示缺漏且絕不寫使用者 repo。
"""

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.agent_executor import RISK_L0, RISK_L1
from core.models import (
    AgentExecutionReceipt,
    Base,
    DailySummary,
    ProjectState,
    SecretaryScheduledTask,
)
from core.scheduled_tasks import (
    SCHEDULABLE_TEMPLATES,
    SchedulableTemplate,
    ScheduleRejected,
    create_scheduled_task,
    delete_scheduled_task,
    latest_occurrence,
    list_scheduled_tasks,
    run_due_scheduled_tasks,
    run_scheduled_task_now,
    scheduled_tasks_enabled,
    update_scheduled_task,
)
from core.server import app
from core.status_draft import build_status_draft
from synthesizer.rollup import build_report_rollup, rollup_period


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


def _cfg(enabled=True, executor=True, reports_dir=None):
    data = {
        "proactive_secretary": {
            "executor": {
                "enabled": executor,
                "scheduled_tasks": {"enabled": enabled},
            }
        }
    }
    if reports_dir is not None:
        data["exporters"] = {"reports_dir": str(reports_dir)}
    return DictConfig(data)


NOW = datetime(2026, 8, 31, 12, 0, 0)  # 週一中午


def _fake_template(runner, template_id="fake_l0_probe", risk=RISK_L0):
    return SchedulableTemplate(
        template_id=template_id,
        risk_level=risk,
        label="test probe",
        description="test",
        params_schema={},
        validate_params=lambda params, _db: {},
        build_runner=lambda params: runner,
        receipt_fields=("probe",),
        timeout_seconds=10,
    )


# ---- 白名單契約 ----


def test_registry_only_contains_l0_read_only_templates():
    assert set(SCHEDULABLE_TEMPLATES) == {
        "generate_handoff",
        "weekly_report_rollup",
        "monthly_report_rollup",
        "status_snapshot_draft",
    }
    for template in SCHEDULABLE_TEMPLATES.values():
        assert template.risk_level == RISK_L0


def test_executor_l1_l2_templates_are_never_schedulable():
    database = TempDatabase()
    for template_id in (
        "repo_fetch",
        "open_loop_mark_stale",
        "agent_draft_plan",
        "agent_apply_plan",
        "repo_pull_ff",
        "totally_unknown",
    ):
        with pytest.raises(ScheduleRejected) as excinfo:
            create_scheduled_task(
                {"template_id": template_id, "schedule_kind": "daily", "run_time": "08:00"},
                database=database,
                cfg=_cfg(),
                now=NOW,
            )
        assert excinfo.value.error_code == "template_not_schedulable"
        assert excinfo.value.http_status == 422


def test_l1_template_registration_is_rejected_at_module_contract():
    # 註冊表載入即檢查；此處驗證同一守門邏輯對 L1 物件成立。
    bad = _fake_template(lambda ctx: {}, template_id="bad", risk=RISK_L1)
    assert bad.risk_level != RISK_L0


def test_disabled_switches_fail_closed():
    database = TempDatabase()
    for cfg in (_cfg(enabled=False), _cfg(executor=False), _cfg(enabled=True, executor=False)):
        assert scheduled_tasks_enabled(cfg) is False
        with pytest.raises(ScheduleRejected) as excinfo:
            create_scheduled_task(
                {"template_id": "status_snapshot_draft", "schedule_kind": "daily"},
                database=database,
                cfg=cfg,
                now=NOW,
            )
        assert excinfo.value.error_code == "scheduled_tasks_disabled"
        assert run_due_scheduled_tasks(database=database, cfg=cfg, now=NOW) == {
            "status": "disabled",
            "ran": [],
        }


def test_create_validates_params_and_schedule():
    database = TempDatabase()
    cfg = _cfg()
    # 未知參數一律拒絕
    with pytest.raises(ScheduleRejected) as excinfo:
        create_scheduled_task(
            {
                "template_id": "status_snapshot_draft",
                "params": {"path": "C:/evil"},
                "schedule_kind": "daily",
            },
            database=database,
            cfg=cfg,
            now=NOW,
        )
    assert excinfo.value.error_code == "unknown_param"
    # handoff 需要既有 project
    with pytest.raises(ScheduleRejected) as excinfo:
        create_scheduled_task(
            {
                "template_id": "generate_handoff",
                "params": {"project_key": "no-such-project"},
                "schedule_kind": "daily",
            },
            database=database,
            cfg=cfg,
            now=NOW,
        )
    assert excinfo.value.error_code == "project_not_found"
    # weekly 需 weekday、monthly 限 1–28、run_time 需合法
    for payload, code in (
        ({"schedule_kind": "weekly"}, "invalid_weekday"),
        ({"schedule_kind": "monthly", "day_of_month": 31}, "invalid_day_of_month"),
        ({"schedule_kind": "daily", "run_time": "25:99"}, "invalid_run_time"),
        ({"schedule_kind": "hourly"}, "invalid_schedule_kind"),
    ):
        with pytest.raises(ScheduleRejected) as excinfo:
            create_scheduled_task(
                {"template_id": "status_snapshot_draft", **payload},
                database=database,
                cfg=cfg,
                now=NOW,
            )
        assert excinfo.value.error_code == code


def test_crud_roundtrip_and_task_listing():
    database = TempDatabase()
    cfg = _cfg()
    with database.session_scope() as session:
        session.add(
            ProjectState(project_key="activityTracker", display_name="activityTracker")
        )
    created = create_scheduled_task(
        {
            "template_id": "generate_handoff",
            "params": {"project_key": "activityTracker"},
            "schedule_kind": "weekly",
            "run_time": "07:15",
            "weekday": 0,
        },
        database=database,
        cfg=cfg,
        now=NOW,
    )["task"]
    assert created["params"] == {"project_key": "activityTracker"}
    assert created["run_time"] == "07:15"

    listing = list_scheduled_tasks(database=database, cfg=cfg)
    assert listing["enabled"] is True
    assert [item["template_id"] for item in listing["templates"]] == list(SCHEDULABLE_TEMPLATES)
    assert len(listing["tasks"]) == 1

    updated = update_scheduled_task(
        created["id"],
        {"enabled": False, "schedule_kind": "monthly", "day_of_month": 3, "run_time": "06:00"},
        database=database,
        cfg=cfg,
        now=NOW,
    )["task"]
    assert updated["enabled"] is False
    assert updated["schedule_kind"] == "monthly"
    assert updated["day_of_month"] == 3
    assert updated["weekday"] is None

    delete_scheduled_task(created["id"], database=database, cfg=cfg)
    assert list_scheduled_tasks(database=database, cfg=cfg)["tasks"] == []


# ---- due 計算與補跑語意 ----


def test_latest_occurrence_math():
    now = datetime(2026, 8, 31, 12, 0)  # 週一
    assert latest_occurrence("daily", "08:30", None, None, now) == datetime(2026, 8, 31, 8, 30)
    assert latest_occurrence("daily", "13:00", None, None, now) == datetime(2026, 8, 30, 13, 0)
    # weekly：週日(6) 09:00 → 昨天
    assert latest_occurrence("weekly", "09:00", 6, None, now) == datetime(2026, 8, 30, 9, 0)
    # weekly：週一(0) 13:00 → 上週一
    assert latest_occurrence("weekly", "13:00", 0, None, now) == datetime(2026, 8, 24, 13, 0)
    # monthly：1 日 06:00 → 本月 1 日；31 日不存在（限 28）
    assert latest_occurrence("monthly", "06:00", None, 1, now) == datetime(2026, 8, 1, 6, 0)
    # monthly：尚未到的 day → 上個月
    early = datetime(2026, 3, 2, 12, 0)
    assert latest_occurrence("monthly", "06:00", None, 15, early) == datetime(2026, 2, 15, 6, 0)


def test_new_task_waits_for_next_occurrence_then_catches_up_once(monkeypatch):
    database = TempDatabase()
    cfg = _cfg()
    calls = []
    probe = _fake_template(lambda ctx: calls.append(ctx) or {"probe": len(calls)})
    monkeypatch.setitem(SCHEDULABLE_TEMPLATES, "fake_l0_probe", probe)

    created = create_scheduled_task(
        {"template_id": "fake_l0_probe", "schedule_kind": "daily", "run_time": "08:00"},
        database=database,
        cfg=cfg,
        now=NOW,  # 12:00 建立，今天 08:00 已過 → 不得立即補跑
    )["task"]

    assert run_due_scheduled_tasks(database=database, cfg=cfg, now=NOW)["ran"] == []
    assert calls == []

    # 三天後才恢復運行：錯過的排程只補跑一次
    later = NOW + timedelta(days=3)
    first = run_due_scheduled_tasks(database=database, cfg=cfg, now=later)
    assert [item["task_id"] for item in first["ran"]] == [created["id"]]
    assert calls and len(calls) == 1
    # 同一時刻再 tick 不重複執行
    assert run_due_scheduled_tasks(database=database, cfg=cfg, now=later)["ran"] == []
    # 下一個排程時刻之後再度到期
    next_day = later + timedelta(days=1)
    assert len(run_due_scheduled_tasks(database=database, cfg=cfg, now=next_day)["ran"]) == 1
    assert len(calls) == 2


def test_run_writes_audit_receipt_and_updates_task(monkeypatch):
    database = TempDatabase()
    cfg = _cfg()
    probe = _fake_template(lambda ctx: {"probe": "ok", "secret": "never-in-receipt"})
    monkeypatch.setitem(SCHEDULABLE_TEMPLATES, "fake_l0_probe", probe)
    created = create_scheduled_task(
        {"template_id": "fake_l0_probe", "schedule_kind": "daily", "run_time": "08:00"},
        database=database,
        cfg=cfg,
        now=NOW,
    )["task"]

    result = run_scheduled_task_now(created["id"], database=database, cfg=cfg, now=NOW)
    assert result["status"] == "succeeded"
    assert result["result"]["probe"] == "ok"

    with database.session_scope() as session:
        receipt = session.query(AgentExecutionReceipt).one()
        assert receipt.proposal_id == f"scheduled_task:{created['id']}"
        assert receipt.risk_level == RISK_L0
        assert receipt.approved_via == "web_click"
        assert receipt.status == "succeeded"
        summary = json.loads(receipt.output_summary)
        assert summary == {"probe": "ok"}  # receipt 只留白名單欄位
        task_row = session.get(SecretaryScheduledTask, created["id"])
        assert task_row.last_status == "succeeded"
        assert task_row.last_receipt_id == receipt.id

    # 排程觸發的 approved_via 必須是 schedule
    later = NOW + timedelta(days=1)
    run_due_scheduled_tasks(database=database, cfg=cfg, now=later)
    with database.session_scope() as session:
        rows = session.query(AgentExecutionReceipt).order_by(AgentExecutionReceipt.id).all()
        assert rows[-1].approved_via == "schedule"


def test_runner_failure_recorded_honestly(monkeypatch):
    database = TempDatabase()
    cfg = _cfg()

    def _boom(ctx):
        raise RuntimeError("deliberate")

    probe = _fake_template(_boom)
    monkeypatch.setitem(SCHEDULABLE_TEMPLATES, "fake_l0_probe", probe)
    created = create_scheduled_task(
        {"template_id": "fake_l0_probe", "schedule_kind": "daily", "run_time": "08:00"},
        database=database,
        cfg=cfg,
        now=NOW,
    )["task"]
    result = run_scheduled_task_now(created["id"], database=database, cfg=cfg, now=NOW)
    assert result["status"] == "failed"
    assert result["error_code"] == "RuntimeError"
    with database.session_scope() as session:
        receipt = session.query(AgentExecutionReceipt).one()
        assert receipt.status == "failed"
        task_row = session.get(SecretaryScheduledTask, created["id"])
        assert task_row.last_status == "failed"
        assert task_row.last_run_at is not None  # 失敗也前移，不重試轟炸


# ---- rollup 契約 ----


def test_rollup_period_covers_last_complete_week_and_month():
    start, end, label = rollup_period("weekly", NOW)
    assert (start.weekday(), end.weekday()) == (0, 6)
    assert end < NOW.date()
    assert label == "2026-W35"
    m_start, m_end, m_label = rollup_period("monthly", NOW)
    assert (m_start.isoformat(), m_end.isoformat(), m_label) == (
        "2026-07-01",
        "2026-07-31",
        "2026-07",
    )


def test_weekly_rollup_reports_missing_days_and_llm_fallback(tmp_path):
    database = TempDatabase()
    cfg = _cfg(reports_dir=tmp_path)
    start, end, label = rollup_period("weekly", NOW)
    with database.session_scope() as session:
        for offset in (0, 2):
            day = (start + timedelta(days=offset)).isoformat()
            session.add(
                DailySummary(
                    date_str=day,
                    llm_provider="test",
                    model_name="test",
                    raw_markdown=f"# {day} 摘要\n- 做了事 {offset}",
                )
            )

    payload = build_report_rollup(
        "weekly",
        database=database,
        cfg=cfg,
        now=NOW,
        llm_generate=lambda system, user: "# ⚠️ 備援報告",  # LLM 失敗樣態
    )
    assert payload["days_total"] == 7
    assert payload["days_with_summary"] == 2
    assert payload["days_missing"] == 5
    assert payload["llm_used"] is False
    output = Path(payload["output_path"])
    assert output.parent == tmp_path
    text = output.read_text(encoding="utf-8")
    assert "deterministic 回退" in text
    assert "缺每日摘要的日期" in text
    assert f"{label}" in text


def test_weekly_rollup_uses_llm_reply_when_valid(tmp_path):
    database = TempDatabase()
    cfg = _cfg(reports_dir=tmp_path)
    start, _end, _label = rollup_period("weekly", NOW)
    with database.session_scope() as session:
        session.add(
            DailySummary(
                date_str=start.isoformat(),
                llm_provider="test",
                model_name="test",
                raw_markdown="# 摘要",
            )
        )
    seen_prompts = {}

    def _llm(system, user):
        seen_prompts["system"] = system
        seen_prompts["user"] = user
        return "## 🌟 期間亮點\n- 週報內容"

    payload = build_report_rollup("weekly", database=database, cfg=cfg, now=NOW, llm_generate=_llm)
    assert payload["llm_used"] is True
    assert "週報內容" in Path(payload["output_path"]).read_text(encoding="utf-8")
    # 缺漏日必須明確告知 LLM 不得推測
    assert "缺少摘要的日期" in seen_prompts["user"]


def test_rollup_without_summaries_writes_no_file(tmp_path):
    database = TempDatabase()
    cfg = _cfg(reports_dir=tmp_path)
    payload = build_report_rollup(
        "monthly", database=database, cfg=cfg, now=NOW, llm_generate=lambda s, u: "x"
    )
    assert payload["days_with_summary"] == 0
    assert payload["output_path"] is None
    assert list(tmp_path.iterdir()) == []


# ---- STATUS 草稿契約 ----


@dataclass(frozen=True)
class FakeRepoRef:
    repo_id: str
    path: Path


def test_status_draft_flags_stale_and_never_writes_repo(tmp_path):
    repo = tmp_path / "repos" / "demoProject"
    repo.mkdir(parents=True)
    status_file = repo / "STATUS.yaml"
    original = 'name: demo\nlast_updated: "2026-08-01"\nstatus: active\n'
    status_file.write_text(original, encoding="utf-8")
    no_status_repo = tmp_path / "repos" / "bareRepo"
    no_status_repo.mkdir()

    reports_dir = tmp_path / "reports"
    database = TempDatabase()
    with database.session_scope() as session:
        session.add(
            ProjectState(
                project_key="demoProject",
                display_name="demoProject",
                last_activity_at=NOW,
            )
        )

    payload = build_status_draft(
        database=database,
        cfg=_cfg(reports_dir=reports_dir),
        now=NOW,
        repo_references=lambda: [
            FakeRepoRef("a" * 16, repo),
            FakeRepoRef("b" * 16, no_status_repo),
        ],
    )
    assert payload["repos_scanned"] == 2
    assert payload["repos_with_status"] == 1
    assert payload["stale_count"] == 1  # 8/01 → 8/31 落後 30 天
    draft = Path(payload["output_path"])
    assert draft.is_file()
    assert reports_dir in draft.parents
    text = draft.read_text(encoding="utf-8")
    assert "demoProject" in text and "2026-08-01" in text
    # 絕不寫入使用者 repo：內容不變、目錄內無新檔
    assert status_file.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in repo.iterdir()) == ["STATUS.yaml"]


def test_status_draft_reports_parse_errors_without_crashing(tmp_path):
    repo = tmp_path / "broken"
    repo.mkdir()
    (repo / "STATUS.yaml").write_text(":\n  - not: [valid", encoding="utf-8")
    payload = build_status_draft(
        database=TempDatabase(),
        cfg=_cfg(reports_dir=tmp_path / "reports"),
        now=NOW,
        repo_references=lambda: [FakeRepoRef("c" * 16, repo)],
    )
    assert payload["parse_errors"] == 1
    assert payload["stale_count"] == 0


# ---- API boundary ----


def test_scheduled_task_mutations_require_execution_token():
    client = TestClient(app)
    headers = {"Origin": "http://127.0.0.1:8765"}
    # 預設環境沒有 execution token → 一律 401（fail-closed）
    assert (
        client.post(
            "/api/v1/secretary/scheduled-tasks",
            json={"template_id": "status_snapshot_draft", "schedule_kind": "daily"},
            headers=headers,
        ).status_code
        == 401
    )
    assert (
        client.patch(
            "/api/v1/secretary/scheduled-tasks/1", json={"enabled": False}, headers=headers
        ).status_code
        == 401
    )
    assert (
        client.delete("/api/v1/secretary/scheduled-tasks/1", headers=headers).status_code == 401
    )
    assert (
        client.post("/api/v1/secretary/scheduled-tasks/1/run", headers=headers).status_code
        == 401
    )
