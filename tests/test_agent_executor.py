import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.agent_executor import (
    ActionPlan,
    ExecutionRejected,
    ExecutorServices,
    RISK_L2,
    attach_execution_actions,
    cancel_execution,
    derive_action,
    execute_proposal,
    list_execution_receipts,
)
from core.models import AgentExecutionReceipt, Base
from core.repo_sync import RepositorySyncRejected
from core.security import execution_authorized
from core.server import app


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


@dataclass(frozen=True)
class FakeRepoRef:
    repo_id: str
    path: Path


def _config(enabled=True):
    return DictConfig(
        {"proactive_secretary": {"executor": {"enabled": enabled}}}
    )


def _services(**overrides):
    calls = {"repo": [], "loops": []}

    def repo_execute(repo_id, action):
        calls["repo"].append((repo_id, action))
        return {
            "repo_id": repo_id,
            "repo_name": "activityTracker",
            "action": action,
            "status": "success",
            "return_code": 0,
            "output": "Fetching origin\nremote: done",
            "before": {"secret": "should_not_leak"},
            "after": {"secret": "should_not_leak"},
        }

    def loop_transition(loop_id, status, note=None):
        calls["loops"].append((loop_id, status, note))
        return {"loop_id": loop_id, "status": status}

    services = ExecutorServices(
        repo_references=overrides.get(
            "repo_references",
            lambda: [FakeRepoRef("repo123", Path("D:/proj/activityTracker"))],
        ),
        repo_execute=overrides.get("repo_execute", repo_execute),
        build_handoff=overrides.get("build_handoff", lambda key: {"project": key}),
        format_handoff=overrides.get(
            "format_handoff", lambda data: f"# Handoff for {data['project']}\n內容"
        ),
        loop_transition=overrides.get("loop_transition", loop_transition),
    )
    return services, calls


def _pr_proposal(project="activityTracker"):
    return {
        "proposal_id": "pr-prop-1",
        "proposal_type": "aging_pr",
        "project_key": project,
        "evidence_refs": [f"pr:{project}#9"],
    }


def _loop_proposal(refs=("project_states:9", "open_loops:12")):
    return {
        "proposal_id": "loop-prop-1",
        "proposal_type": "stalled_open_loop",
        "project_key": "AI_Papers",
        "evidence_refs": list(refs),
    }


def _extension_proposal():
    return {
        "proposal_id": "ext-prop-1",
        "proposal_type": "verify_extension_heartbeat",
        "project_key": "OmniContext",
        "evidence_refs": ["extension_status:live"],
    }


def _lookup(proposal):
    return lambda proposal_id, **_: proposal if proposal["proposal_id"] == proposal_id else None


def test_executor_disabled_rejects_everything():
    database = TempDatabase()
    services, _ = _services()
    with pytest.raises(ExecutionRejected) as excinfo:
        execute_proposal(
            "pr-prop-1",
            database=database,
            cfg=_config(enabled=False),
            services=services,
            proposal_lookup=_lookup(_pr_proposal()),
        )
    assert excinfo.value.error_code == "executor_disabled"


def test_unknown_or_expired_proposal_is_404():
    database = TempDatabase()
    services, _ = _services()
    with pytest.raises(ExecutionRejected) as excinfo:
        execute_proposal(
            "no-such-id",
            database=database,
            cfg=_config(),
            services=services,
            proposal_lookup=lambda *_args, **_kw: None,
        )
    assert excinfo.value.error_code == "proposal_not_found_or_expired"
    assert excinfo.value.http_status == 404


def test_extension_proposal_has_no_registered_action():
    database = TempDatabase()
    services, _ = _services()
    assert derive_action(_extension_proposal(), services=services) is None
    with pytest.raises(ExecutionRejected) as excinfo:
        execute_proposal(
            "ext-prop-1",
            database=database,
            cfg=_config(),
            services=services,
            proposal_lookup=_lookup(_extension_proposal()),
        )
    assert excinfo.value.error_code == "no_registered_action"


def test_repo_fetch_executes_whitelisted_action_and_writes_receipt():
    database = TempDatabase()
    services, calls = _services()

    response = execute_proposal(
        "pr-prop-1",
        database=database,
        cfg=_config(),
        services=services,
        proposal_lookup=_lookup(_pr_proposal()),
        now=datetime(2026, 8, 31, 10, 0),
    )

    assert calls["repo"] == [("repo123", "fetch")]
    receipt = response["receipt"]
    assert receipt["template_id"] == "repo_fetch"
    assert receipt["risk_level"] == "L1_ASSIST"
    assert receipt["status"] == "succeeded"
    assert receipt["output_digest"]
    assert "repo_sync.execute" in receipt["action_call"]
    # receipt 摘要只含白名單欄位，不含 before/after 或 secrets
    summary = json.loads(receipt["output_summary"])
    assert set(summary) == {"repo_name", "action", "status", "return_code"}
    assert "should_not_leak" not in receipt["output_summary"]
    # DB row 存在
    with database.session_scope() as session:
        rows = session.query(AgentExecutionReceipt).all()
        assert len(rows) == 1
        assert rows[0].status == "succeeded"


def test_ambiguous_repo_name_falls_back_to_handoff():
    refs = [
        FakeRepoRef("a", Path("D:/x/activityTracker")),
        FakeRepoRef("b", Path("E:/y/activityTracker")),
    ]
    services, _ = _services(repo_references=lambda: refs)
    plan = derive_action(_pr_proposal(), services=services)
    assert plan.template_id == "generate_handoff"
    assert plan.risk_level == "L0_READ_ONLY"


def test_handoff_response_contains_markdown_but_receipt_only_counts():
    database = TempDatabase()
    services, _ = _services()
    proposal = _loop_proposal(refs=("project_states:9", "open_loops:1", "open_loops:2"))  # 多 ref → handoff

    response = execute_proposal(
        "loop-prop-1",
        database=database,
        cfg=_config(),
        services=services,
        proposal_lookup=_lookup(proposal),
    )

    receipt = response["receipt"]
    assert receipt["template_id"] == "generate_handoff"
    assert response["result"]["handoff_markdown"].startswith("# Handoff")
    assert "handoff_markdown" not in (receipt["output_summary"] or "")
    summary = json.loads(receipt["output_summary"])
    assert summary["project_key"] == "AI_Papers"
    assert summary["handoff_chars"] > 0


def test_single_open_loop_proposal_marks_stale():
    database = TempDatabase()
    services, calls = _services()

    response = execute_proposal(
        "loop-prop-1",
        database=database,
        cfg=_config(),
        services=services,
        proposal_lookup=_lookup(_loop_proposal()),
    )

    assert calls["loops"] == [(12, "stale", "via secretary executor")]
    assert response["receipt"]["template_id"] == "open_loop_mark_stale"
    assert json.loads(response["receipt"]["output_summary"]) == {
        "loop_id": 12,
        "status": "stale",
    }


def test_precondition_rejection_becomes_failed_receipt():
    database = TempDatabase()

    def rejecting_repo(repo_id, action):
        raise RepositorySyncRejected("worktree 有未提交變更")

    services, _ = _services(repo_execute=rejecting_repo)
    response = execute_proposal(
        "pr-prop-1",
        database=database,
        cfg=_config(),
        services=services,
        proposal_lookup=_lookup(_pr_proposal()),
    )
    receipt = response["receipt"]
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "RepositorySyncRejected"
    assert "result" not in response


def test_timeout_is_a_first_class_receipt_status():
    database = TempDatabase()
    services, _ = _services()
    slow_plan = ActionPlan(
        template_id="slow_template",
        risk_level="L0_READ_ONLY",
        label="slow",
        call_description="slow()",
        params={},
        timeout_seconds=1,
        receipt_fields=(),
        runner=lambda: time.sleep(3) or {},
    )
    import core.agent_executor as executor_module

    original = executor_module.derive_action
    executor_module.derive_action = lambda proposal, services: slow_plan
    try:
        response = execute_proposal(
            "pr-prop-1",
            database=database,
            cfg=_config(),
            services=services,
            proposal_lookup=_lookup(_pr_proposal()),
        )
    finally:
        executor_module.derive_action = original
    assert response["receipt"]["status"] == "timeout"
    assert response["receipt"]["error_code"] == "execution_timeout"


def test_l2_is_rejected_without_confirmation_mechanism():
    database = TempDatabase()
    services, _ = _services()
    l2_plan = ActionPlan(
        template_id="mutate_something",
        risk_level=RISK_L2,
        label="mutate",
        call_description="mutate()",
        params={},
        timeout_seconds=5,
        receipt_fields=(),
        runner=lambda: {},
    )
    import core.agent_executor as executor_module

    original = executor_module.derive_action
    executor_module.derive_action = lambda proposal, services: l2_plan
    try:
        with pytest.raises(ExecutionRejected) as excinfo:
            execute_proposal(
                "pr-prop-1",
                database=database,
                cfg=_config(),
                services=services,
                proposal_lookup=_lookup(_pr_proposal()),
            )
    finally:
        executor_module.derive_action = original
    assert excinfo.value.error_code == "l2_confirmation_not_available"


def test_active_execution_is_deduplicated():
    database = TempDatabase()
    services, _ = _services()
    with database.session_scope() as session:
        session.add(
            AgentExecutionReceipt(
                proposal_id="pr-prop-1",
                template_id="repo_fetch",
                risk_level="L1_ASSIST",
                action_call="repo_sync.execute('repo123', 'fetch')",
                status="running",
                requested_at=datetime(2026, 8, 31, 9, 59),
                started_at=datetime(2026, 8, 31, 9, 59),
            )
        )
    with pytest.raises(ExecutionRejected) as excinfo:
        execute_proposal(
            "pr-prop-1",
            database=database,
            cfg=_config(),
            services=services,
            proposal_lookup=_lookup(_pr_proposal()),
        )
    assert excinfo.value.error_code == "execution_already_running"


def test_cancel_contract_for_running_and_finished():
    database = TempDatabase()
    with database.session_scope() as session:
        session.add(
            AgentExecutionReceipt(
                id=1,
                proposal_id="p1",
                template_id="repo_fetch",
                risk_level="L1_ASSIST",
                action_call="x",
                status="running",
                requested_at=datetime(2026, 8, 31, 9, 0),
            )
        )
        session.add(
            AgentExecutionReceipt(
                id=2,
                proposal_id="p2",
                template_id="repo_fetch",
                risk_level="L1_ASSIST",
                action_call="x",
                status="succeeded",
                requested_at=datetime(2026, 8, 31, 9, 1),
            )
        )
        session.add(
            AgentExecutionReceipt(
                id=3,
                proposal_id="p3",
                template_id="repo_fetch",
                risk_level="L1_ASSIST",
                action_call="x",
                status="queued",
                requested_at=datetime(2026, 8, 31, 9, 2),
            )
        )

    with pytest.raises(ExecutionRejected) as running:
        cancel_execution(1, database=database)
    assert running.value.error_code == "not_cancellable_in_process"
    with pytest.raises(ExecutionRejected) as finished:
        cancel_execution(2, database=database)
    assert finished.value.error_code == "execution_already_finished"
    cancelled = cancel_execution(3, database=database)
    assert cancelled["receipt"]["status"] == "cancelled"
    listing = list_execution_receipts(10, database=database)
    assert len(listing["receipts"]) == 3


def test_attach_actions_is_noop_when_disabled_and_marks_when_enabled():
    services, _ = _services()
    base = {
        "status": "proposal_only",
        "proposals": [_pr_proposal(), _extension_proposal()],
        "execution_available": False,
    }
    unchanged = attach_execution_actions(
        json.loads(json.dumps(base)), cfg=_config(enabled=False), services=services
    )
    assert unchanged["execution_available"] is False
    assert "executor" not in unchanged
    assert all("action" not in item for item in unchanged["proposals"])

    marked = attach_execution_actions(
        json.loads(json.dumps(base)), cfg=_config(enabled=True), services=services
    )
    assert marked["execution_available"] is True
    assert marked["executor"]["enabled"] is True
    assert marked["executor"]["l2_available"] is False
    by_id = {item["proposal_id"]: item for item in marked["proposals"]}
    assert by_id["pr-prop-1"]["execution_available"] is True
    assert by_id["pr-prop-1"]["action"]["template_id"] == "repo_fetch"
    assert by_id["ext-prop-1"].get("execution_available") is not True


def test_execution_token_is_fail_closed():
    cfg = DictConfig({"security": {"execution_token": "secret-token"}})
    assert execution_authorized("secret-token", cfg) is True
    assert execution_authorized("wrong", cfg) is False
    assert execution_authorized(None, cfg) is False
    empty = DictConfig({"security": {}})
    assert execution_authorized("anything", empty) is False


def test_execute_endpoint_requires_token_and_ignores_request_body():
    client = TestClient(app)
    # 預設環境沒有 execution token → 一律 401（fail-closed），
    # 即使呼叫端試圖夾帶 command 也毫無效果。
    response = client.post(
        "/api/v1/secretary/proposals/whatever/execute",
        json={"command": "rm -rf /", "argv": ["evil"]},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 401


def test_no_shell_subprocess_anywhere_in_core():
    """ADR-008 acceptance #2：程式庫不得出現 create_subprocess_shell。"""
    core_dir = Path(__file__).resolve().parents[1] / "core"
    offenders = [
        path.name
        for path in core_dir.glob("*.py")
        if "create_subprocess_shell" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    executor_source = (core_dir / "agent_executor.py").read_text(encoding="utf-8")
    assert "import subprocess" not in executor_source
    assert "create_subprocess" not in executor_source
