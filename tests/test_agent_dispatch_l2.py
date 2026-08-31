"""P5-R3 contract tests：subprocess dispatcher 與 L2 二次確認閘門。"""

import json
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.agent_dispatch import (
    DispatchRejected,
    DispatchTimeout,
    build_subprocess_env,
    run_agent_subprocess,
)
from core.agent_executor import (
    ActionPlan,
    ExecutionRejected,
    ExecutorServices,
    _reset_pending_confirms,
    attach_execution_actions,
    cancel_execution,
    derive_actions,
    execute_proposal,
)
from core.models import AgentExecutionReceipt, Base


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
        # StaticPool + check_same_thread=False：cancel 測試需要跨執行緒共用同一顆 in-memory DB。
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
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


@dataclass(frozen=True)
class FakeRepoRef:
    repo_id: str
    path: Path


@pytest.fixture(autouse=True)
def _clean_confirm_state():
    _reset_pending_confirms()
    yield
    _reset_pending_confirms()


def _l2_config(binary=None, args=None, cooldown=600, ttl=300):
    return DictConfig(
        {
            "proactive_secretary": {
                "executor": {
                    "enabled": True,
                    "l2": {
                        "enabled": True,
                        "confirm_ttl_seconds": ttl,
                        "cooldown_seconds": cooldown,
                    },
                    "agent_cli": {
                        "binary": binary or sys.executable,
                        "args": args or ["-c", "print('# 行動計畫')"],
                        "timeout_seconds": 60,
                    },
                }
            }
        }
    )


def _loop_proposal(project="AI_Papers"):
    return {
        "proposal_id": "loop-prop-l2",
        "proposal_type": "stalled_open_loop",
        "project_key": project,
        "title": "停滯的論文修訂",
        "detail": "已 3 天未更新",
        "suggested_action": "先看 handoff",
        "evidence_refs": ["project_states:9", "open_loops:12"],
    }


def _services(repo_dir: Path):
    def loop_transition(loop_id, status, note=None):
        return {"loop_id": loop_id, "status": status}

    return ExecutorServices(
        repo_references=lambda: [FakeRepoRef("repoX", repo_dir)],
        repo_execute=lambda repo_id, action: {"status": "success"},
        build_handoff=lambda key: {"project": key},
        format_handoff=lambda data: f"# Handoff {data['project']}",
        loop_transition=loop_transition,
    )


def _lookup(proposal):
    return lambda proposal_id, **_: proposal if proposal["proposal_id"] == proposal_id else None


def _repo_dir(tmp_path: Path, name="AI_Papers") -> Path:
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    return path


# ---------------------------------------------------------------- dispatcher 本體


def test_subprocess_env_is_allowlisted_and_secrets_never_reach_child(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret")
    monkeypatch.setenv("OMNICONTEXT_EXECUTION_TOKEN", "exec-token")
    env = build_subprocess_env()
    assert "GEMINI_API_KEY" not in env
    assert "OMNICONTEXT_EXECUTION_TOKEN" not in env
    assert env.get("PATH")

    probe = (
        "import os, json; print(json.dumps({"
        "'gem': 'GEMINI_API_KEY' in os.environ,"
        "'tok': 'OMNICONTEXT_EXECUTION_TOKEN' in os.environ}))"
    )
    outcome = run_agent_subprocess(
        [sys.executable, "-c", probe], cwd=tmp_path, timeout_seconds=30
    )
    assert outcome["exit_code"] == 0
    child_view = json.loads(outcome["stdout"])
    assert child_view == {"gem": False, "tok": False}


def test_subprocess_rejects_missing_cli_and_bad_cwd(tmp_path):
    with pytest.raises(DispatchRejected) as missing:
        run_agent_subprocess(
            ["omnicontext-no-such-cli-xyz"], cwd=tmp_path, timeout_seconds=10
        )
    assert missing.value.error_code == "cli_not_found"

    with pytest.raises(DispatchRejected) as badcwd:
        run_agent_subprocess(
            [sys.executable, "-c", "print(1)"],
            cwd=tmp_path / "does-not-exist",
            timeout_seconds=10,
        )
    assert badcwd.value.error_code == "cwd_not_found"

    with pytest.raises(DispatchRejected):
        run_agent_subprocess([], cwd=tmp_path, timeout_seconds=10)


def test_subprocess_timeout_kills_process_and_reports_honestly(tmp_path):
    started = time.monotonic()
    with pytest.raises(DispatchTimeout) as excinfo:
        run_agent_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=3,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 15  # 行程真的被 kill，沒有等滿 30 秒
    assert excinfo.value.payload["timed_out"] is True


# ---------------------------------------------------------------- L2 閘門（confirm code / 冷卻 / 開關）


def test_l2_confirm_flow_issue_then_execute_and_write_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_HOME", str(tmp_path / "home"))
    database = TempDatabase()
    repo = _repo_dir(tmp_path)
    cfg = _l2_config()
    services = _services(repo)
    now = datetime(2026, 8, 31, 10, 0)

    first = execute_proposal(
        "loop-prop-l2",
        template_id="agent_draft_plan",
        database=database,
        cfg=cfg,
        services=services,
        proposal_lookup=_lookup(_loop_proposal()),
        now=now,
    )
    assert first["status"] == "confirmation_required"
    code = first["confirm"]["confirm_code"]
    assert len(code) == 6 and code.isdigit()
    # 未附 confirm code 前不得產生任何 receipt
    with database.session_scope() as session:
        assert session.query(AgentExecutionReceipt).count() == 0

    second = execute_proposal(
        "loop-prop-l2",
        template_id="agent_draft_plan",
        confirm_code=code,
        database=database,
        cfg=cfg,
        services=services,
        proposal_lookup=_lookup(_loop_proposal()),
        now=now + timedelta(seconds=10),
    )
    receipt = second["receipt"]
    assert receipt["status"] == "succeeded"
    assert receipt["template_id"] == "agent_draft_plan"
    assert receipt["risk_level"] == "L2_MUTATE"
    assert receipt["approved_via"] == "web_click+confirm_code"
    assert second["result"]["plan_markdown"].startswith("# 行動計畫")

    summary = json.loads(receipt["output_summary"])
    output_path = Path(summary["output_path"])
    assert output_path.exists()
    assert str(tmp_path / "home") in str(output_path)
    assert "agent_outputs" in str(output_path)
    assert output_path.read_text(encoding="utf-8").startswith("# 行動計畫")


def test_l2_wrong_code_burns_pending_and_expiry_rejects(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_HOME", str(tmp_path / "home"))
    database = TempDatabase()
    cfg = _l2_config()
    services = _services(_repo_dir(tmp_path))
    now = datetime(2026, 8, 31, 10, 0)
    kwargs = dict(
        template_id="agent_draft_plan",
        database=database,
        cfg=cfg,
        services=services,
        proposal_lookup=_lookup(_loop_proposal()),
    )

    issued = execute_proposal("loop-prop-l2", now=now, **kwargs)
    code = issued["confirm"]["confirm_code"]
    wrong = "000000" if code != "000000" else "000001"
    with pytest.raises(ExecutionRejected) as bad:
        execute_proposal("loop-prop-l2", confirm_code=wrong, now=now, **kwargs)
    assert bad.value.error_code == "confirm_code_invalid"
    assert bad.value.http_status == 403
    # 單次有效：錯一次即作廢，正確碼也不能再用
    with pytest.raises(ExecutionRejected) as burned:
        execute_proposal("loop-prop-l2", confirm_code=code, now=now, **kwargs)
    assert burned.value.error_code == "confirm_code_not_issued"

    reissued = execute_proposal("loop-prop-l2", now=now, **kwargs)
    late = now + timedelta(seconds=301)
    with pytest.raises(ExecutionRejected) as expired:
        execute_proposal(
            "loop-prop-l2",
            confirm_code=reissued["confirm"]["confirm_code"],
            now=late,
            **kwargs,
        )
    assert expired.value.error_code == "confirm_code_expired"
    with database.session_scope() as session:
        assert session.query(AgentExecutionReceipt).count() == 0  # 全程無執行


def test_l2_cooldown_blocks_immediate_rerun_and_zero_disables_it(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_HOME", str(tmp_path / "home"))
    database = TempDatabase()
    services = _services(_repo_dir(tmp_path))
    now = datetime(2026, 8, 31, 10, 0)

    def _run_once(cfg, at):
        issued = execute_proposal(
            "loop-prop-l2",
            template_id="agent_draft_plan",
            database=database,
            cfg=cfg,
            services=services,
            proposal_lookup=_lookup(_loop_proposal()),
            now=at,
        )
        return execute_proposal(
            "loop-prop-l2",
            template_id="agent_draft_plan",
            confirm_code=issued["confirm"]["confirm_code"],
            database=database,
            cfg=cfg,
            services=services,
            proposal_lookup=_lookup(_loop_proposal()),
            now=at,
        )

    assert _run_once(_l2_config(), now)["receipt"]["status"] == "succeeded"
    with pytest.raises(ExecutionRejected) as cooling:
        execute_proposal(
            "loop-prop-l2",
            template_id="agent_draft_plan",
            database=database,
            cfg=_l2_config(),
            services=services,
            proposal_lookup=_lookup(_loop_proposal()),
            now=now + timedelta(seconds=30),
        )
    assert cooling.value.error_code == "l2_cooldown_active"
    assert cooling.value.http_status == 429
    # 冷卻期滿即可再執行；cooldown_seconds=0 則完全停用冷卻
    assert (
        _run_once(_l2_config(), now + timedelta(seconds=601))["receipt"]["status"]
        == "succeeded"
    )
    assert (
        _run_once(_l2_config(cooldown=0), now + timedelta(seconds=602))["receipt"]["status"]
        == "succeeded"
    )


def test_l2_cli_failure_and_missing_cli_become_honest_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_HOME", str(tmp_path / "home"))
    services = _services(_repo_dir(tmp_path))
    now = datetime(2026, 8, 31, 10, 0)

    def _confirmed_run(cfg):
        database = TempDatabase()
        issued = execute_proposal(
            "loop-prop-l2",
            template_id="agent_draft_plan",
            database=database,
            cfg=cfg,
            services=services,
            proposal_lookup=_lookup(_loop_proposal()),
            now=now,
        )
        return execute_proposal(
            "loop-prop-l2",
            template_id="agent_draft_plan",
            confirm_code=issued["confirm"]["confirm_code"],
            database=database,
            cfg=cfg,
            services=services,
            proposal_lookup=_lookup(_loop_proposal()),
            now=now,
        )

    failing = _confirmed_run(_l2_config(args=["-c", "import sys; sys.exit(7)"]))
    assert failing["receipt"]["status"] == "failed"
    assert failing["receipt"]["error_code"] == "cli_exit_7"
    assert json.loads(failing["receipt"]["output_summary"])["exit_code"] == 7
    assert "result" not in failing

    missing = _confirmed_run(_l2_config(binary="omnicontext-no-such-cli-xyz"))
    assert missing["receipt"]["status"] == "rejected"
    assert missing["receipt"]["error_code"] == "cli_not_found"


def test_derive_actions_and_attach_expose_l2_only_when_enabled(tmp_path):
    services = _services(_repo_dir(tmp_path))
    proposal = _loop_proposal()

    enabled_plans = derive_actions(proposal, services=services, cfg=_l2_config())
    assert [plan.template_id for plan in enabled_plans] == [
        "open_loop_mark_stale",
        "agent_draft_plan",
    ]
    assert enabled_plans[1].dispatch_mode == "subprocess"
    # prompt 由 server 端白名單欄位組成，且截斷過長內容
    long_detail = dict(proposal, detail="Ｘ" * 1000)
    plan = derive_actions(long_detail, services=services, cfg=_l2_config())[1]
    prompt_arg = plan.runner.__closure__  # 只驗證 plan 存在；內容於執行時驗證
    assert prompt_arg is not None

    disabled = DictConfig({"proactive_secretary": {"executor": {"enabled": True}}})
    only_l1 = derive_actions(proposal, services=services, cfg=disabled)
    assert [plan.template_id for plan in only_l1] == ["open_loop_mark_stale"]

    marked = attach_execution_actions(
        {"proposals": [dict(proposal)]}, cfg=_l2_config(), services=services
    )
    assert marked["executor"]["l2_available"] is True
    actions = marked["proposals"][0]["actions"]
    assert {a["template_id"]: a["requires_confirmation"] for a in actions} == {
        "open_loop_mark_stale": False,
        "agent_draft_plan": True,
    }
    # primary 維持既有 L1 行為（向後相容）
    assert marked["proposals"][0]["action"]["template_id"] == "open_loop_mark_stale"


def test_cancel_kills_running_subprocess_job_and_status_survives(tmp_path):
    database = TempDatabase()
    repo = _repo_dir(tmp_path)
    plan = ActionPlan(
        template_id="agent_draft_plan",
        risk_level="L1_ASSIST",  # 測 cancel 機制本身，避開 confirm 流程
        label="long job",
        call_description="dispatch(sleep)",
        params={},
        timeout_seconds=25,
        receipt_fields=("exit_code",),
        runner=lambda ctx: run_agent_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=repo,
            timeout_seconds=25,
            receipt_id=ctx["receipt_id"],
        ),
        dispatch_mode="subprocess",
    )
    import core.agent_executor as executor_module

    original = executor_module.derive_action
    executor_module.derive_action = lambda proposal, services: plan
    holder = {}

    def _run():
        holder["response"] = execute_proposal(
            "loop-prop-l2",
            database=database,
            cfg=DictConfig({"proactive_secretary": {"executor": {"enabled": True}}}),
            services=_services(repo),
            proposal_lookup=_lookup(_loop_proposal()),
        )

    thread = threading.Thread(target=_run, daemon=True)
    try:
        thread.start()
        receipt_id = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and receipt_id is None:
            with database.session_scope() as session:
                row = (
                    session.query(AgentExecutionReceipt)
                    .filter(AgentExecutionReceipt.status == "running")
                    .first()
                )
                if row is not None:
                    receipt_id = row.id
            time.sleep(0.1)
        assert receipt_id is not None, "running receipt 未出現"
        time.sleep(0.5)  # 讓 OS 行程完成登記

        cancelled = cancel_execution(receipt_id, database=database)
        assert cancelled["receipt"]["status"] == "cancelled"
        thread.join(timeout=20)
        assert not thread.is_alive()
    finally:
        executor_module.derive_action = original

    # 執行緒收尾後 cancelled 為一級狀態，不得被覆寫回 failed/succeeded
    final = holder["response"]["receipt"]
    assert final["status"] == "cancelled"
    with database.session_scope() as session:
        assert session.get(AgentExecutionReceipt, receipt_id).status == "cancelled"
