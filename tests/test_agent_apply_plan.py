"""ADR-008 Addendum contract tests：agent_apply_plan（L2 寫入型 template）。"""

import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.agent_executor import (
    ExecutionRejected,
    ExecutorServices,
    _agent_cli_write_settings,
    _reset_pending_confirms,
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


@pytest.fixture(autouse=True)
def _clean_confirm_state():
    _reset_pending_confirms()
    yield
    _reset_pending_confirms()


PROJECT = "AI_Papers"
NOW = datetime(2026, 8, 31, 12, 0)

# 假的「會寫檔的 agent CLI」：在 cwd 建兩個檔案並把收到的 prompt 傾印出來。
WRITER = (
    "import sys, pathlib; "
    "pathlib.Path('AGENT_NOTES.md').write_text('agent did work', encoding='utf-8'); "
    "pathlib.Path('prompt_dump.txt').write_text(sys.argv[1], encoding='utf-8'); "
    "print('# 代辦報告: 已完成修改')"
)


def _git(repo: Path, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / PROJECT
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("base", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def _cfg(allow_write=True, cooldown=0):
    return DictConfig(
        {
            "proactive_secretary": {
                "executor": {
                    "enabled": True,
                    "l2": {
                        "enabled": True,
                        "confirm_ttl_seconds": 300,
                        "cooldown_seconds": cooldown,
                        "allow_write": allow_write,
                    },
                    "agent_cli": {
                        "binary": sys.executable,
                        "args": ["-c", "print('draft')"],
                        "write_args": ["-c", WRITER, "{prompt}"],
                        "write_timeout_seconds": 60,
                    },
                }
            }
        }
    )


def _seed_draft_receipt(database, plan_path: Path, *, age_hours=1.0, status="succeeded"):
    plan_path.write_text("測試計畫內容：請新增 AGENT_NOTES 並回報。", encoding="utf-8")
    with database.session_scope() as session:
        session.add(
            AgentExecutionReceipt(
                proposal_id="draft-prop",
                template_id="agent_draft_plan",
                risk_level="L2_MUTATE",
                project_key=PROJECT,
                action_call="draft",
                status=status,
                requested_at=NOW - timedelta(hours=age_hours),
                output_summary=json.dumps({"output_path": str(plan_path)}),
            )
        )


def _proposal():
    return {
        "proposal_id": "loop-prop-apply",
        "proposal_type": "stalled_open_loop",
        "project_key": PROJECT,
        "title": "停滯的論文修訂",
        "detail": "",
        "suggested_action": "",
        "evidence_refs": ["project_states:9", "open_loops:12"],
    }


def _services(repo: Path):
    return ExecutorServices(
        repo_references=lambda: [FakeRepoRef("repoX", repo)],
        repo_execute=lambda *_: {"status": "success"},
        build_handoff=lambda key: {"project": key},
        format_handoff=lambda data: "# Handoff",
        loop_transition=lambda loop_id, status, note=None: {"loop_id": loop_id, "status": status},
    )


def _lookup():
    proposal = _proposal()
    return lambda proposal_id, **_: proposal if proposal["proposal_id"] == proposal_id else None


def test_apply_offered_only_with_write_switch_and_recent_valid_draft(tmp_path):
    repo = _git_repo(tmp_path)
    services = _services(repo)
    database = TempDatabase()

    def _templates(cfg, db):
        return [
            plan.template_id
            for plan in derive_actions(_proposal(), services=services, cfg=cfg, database=db, now=NOW)
        ]

    # 沒開 allow_write → 不提供；開了但沒有近期 draft receipt → 也不提供
    _seed_draft_receipt(database, tmp_path / "plan.md")
    assert "agent_apply_plan" not in _templates(_cfg(allow_write=False), database)
    assert "agent_apply_plan" not in _templates(_cfg(), TempDatabase())
    # 兩者齊備 → 提供，且排在 draft 之後
    assert _templates(_cfg(), database) == [
        "open_loop_mark_stale",
        "agent_draft_plan",
        "agent_apply_plan",
    ]

    # 過期（>24h）或計畫檔已刪 → 一律不提供（A1 fail-closed）
    expired = TempDatabase()
    _seed_draft_receipt(expired, tmp_path / "old.md", age_hours=30)
    assert "agent_apply_plan" not in _templates(_cfg(), expired)
    missing = TempDatabase()
    _seed_draft_receipt(missing, tmp_path / "gone.md")
    (tmp_path / "gone.md").unlink()
    assert "agent_apply_plan" not in _templates(_cfg(), missing)
    failed_draft = TempDatabase()
    _seed_draft_receipt(failed_draft, tmp_path / "bad.md", status="failed")
    assert "agent_apply_plan" not in _templates(_cfg(), failed_draft)


def test_apply_full_flow_writes_files_feeds_plan_and_never_commits(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_HOME", str(tmp_path / "home"))
    repo = _git_repo(tmp_path)
    database = TempDatabase()
    _seed_draft_receipt(database, tmp_path / "plan.md")
    kwargs = dict(
        template_id="agent_apply_plan",
        database=database,
        cfg=_cfg(),
        services=_services(repo),
        proposal_lookup=_lookup(),
        now=NOW,
    )

    issued = execute_proposal("loop-prop-apply", **kwargs)
    assert issued["status"] == "confirmation_required"
    done = execute_proposal(
        "loop-prop-apply", confirm_code=issued["confirm"]["confirm_code"], **kwargs
    )

    receipt = done["receipt"]
    assert receipt["status"] == "succeeded"
    assert receipt["template_id"] == "agent_apply_plan"
    summary = json.loads(receipt["output_summary"])
    assert summary["files_changed"] == 2  # AGENT_NOTES.md + prompt_dump.txt
    assert summary["plan_receipt_id"] >= 1
    # agent 真的改了 worktree
    assert (repo / "AGENT_NOTES.md").read_text(encoding="utf-8") == "agent did work"
    # 已批准的計畫全文餵進 prompt（兩段式的核心）
    assert "測試計畫內容" in (repo / "prompt_dump.txt").read_text(encoding="utf-8")
    # 絕不 commit：git 歷史仍只有 init 一筆，變更留在 worktree
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True, check=True
    )
    assert len(log.stdout.strip().splitlines()) == 1
    # 改動檔名只在回應（receipt 摘要沒有清單，A4）
    assert set(done["result"]["changed_files"]) == {"AGENT_NOTES.md", "prompt_dump.txt"}
    assert "changed_files" not in summary
    assert "git checkout" in done["result"]["claim_boundary"]


def test_apply_rejects_dirty_worktree_before_issuing_confirm_code(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "wip.txt").write_text("使用者未提交的工作", encoding="utf-8")
    database = TempDatabase()
    _seed_draft_receipt(database, tmp_path / "plan.md")
    kwargs = dict(
        template_id="agent_apply_plan",
        database=database,
        cfg=_cfg(),
        services=_services(repo),
        proposal_lookup=_lookup(),
        now=NOW,
    )

    with pytest.raises(ExecutionRejected) as excinfo:
        execute_proposal("loop-prop-apply", **kwargs)
    assert excinfo.value.error_code == "worktree_not_clean"
    # 前置失敗不得發放 confirm code，也不得留下任何 receipt
    with pytest.raises(ExecutionRejected) as no_code:
        execute_proposal("loop-prop-apply", confirm_code="123456", **kwargs)
    assert no_code.value.error_code in {"confirm_code_not_issued", "worktree_not_clean"}
    with database.session_scope() as session:
        rows = session.query(AgentExecutionReceipt).all()
        assert [row.template_id for row in rows] == ["agent_draft_plan"]  # 只有種子


def test_write_settings_have_safe_per_binary_defaults():
    claude_cfg = DictConfig(
        {"proactive_secretary": {"executor": {"agent_cli": {"binary": "claude"}}}}
    )
    binary, args, timeout = _agent_cli_write_settings(claude_cfg)
    assert binary == "claude"
    assert args == ["-p", "{prompt}", "--permission-mode", "acceptEdits"]
    assert timeout == 600

    codex_cfg = DictConfig(
        {"proactive_secretary": {"executor": {"agent_cli": {"binary": "codex"}}}}
    )
    assert _agent_cli_write_settings(codex_cfg)[1] == ["exec", "--full-auto", "{prompt}"]

    # 未知 CLI 又沒明示 write_args → fail-closed 不提供寫入模式
    unknown_cfg = DictConfig(
        {"proactive_secretary": {"executor": {"agent_cli": {"binary": "mystery"}}}}
    )
    assert _agent_cli_write_settings(unknown_cfg) is None
    explicit = DictConfig(
        {
            "proactive_secretary": {
                "executor": {
                    "agent_cli": {"binary": "mystery", "write_args": ["run", "{prompt}"]}
                }
            }
        }
    )
    assert _agent_cli_write_settings(explicit)[1] == ["run", "{prompt}"]
