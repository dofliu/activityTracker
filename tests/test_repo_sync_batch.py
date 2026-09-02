"""ADR-011 Addendum B：Repo 同步全覽、批次動作與小秘書同步報告的契約。

- ``scope=all`` 列出全部 repo（不只近期 N 個），附 summary 與 ``last_fetch_at``。
- ``fetch_all`` 只更新 remote-tracking refs：worktree 檔案內容不變。
- 批次 pull／push 只對「清單內且執行時仍符合前置條件」的 repo 執行；
  批次 push 預設關閉。
- L0 排程 template ``repo_sync_report`` 寫快照與報告、不連網；快照新鮮時
  小秘書產生 ``repo_needs_pull`` 提案，executor 對應 L1 ``repo_pull_ff``。
"""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.agent_executor import (
    RISK_L0,
    RISK_L1,
    ExecutorServices,
    derive_action,
    execute_proposal,
)
from core.models import AgentExecutionReceipt, Base
from core.repo_sync import LocalRepositorySync, RepositorySyncRejected
from core.repo_sync_report import build_repo_sync_report, collect_repo_sync_signals, load_snapshot
from core.scheduled_tasks import SCHEDULABLE_TEMPLATES
from core.server import app


class _Config:
    def __init__(self, root: Path, **overrides):
        self.root = root
        self.overrides = overrides

    def get_paths(self, key: str):
        return [self.root] if key == "watchers.git_watcher.repositories" else []

    def get(self, key: str, default=None):
        values = {
            "watchers.git_watcher.max_depth": 2,
            "repository_sync.command_timeout_seconds": 20,
            "repository_sync.max_repositories": 20,
            "repository_sync.dashboard_recent_limit": 1,
            **self.overrides,
        }
        return values.get(key, default)


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _seed_repo(repo: Path, remote: Path | None, name: str = "seed") -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "OmniContext test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", name)
    if remote is not None:
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _git(repo, "remote", "add", "origin", str(remote))
        _git(repo, "push", "--set-upstream", "origin", "main")


def _advance_remote(tmp_path: Path, remote: Path, tag: str) -> None:
    """在另一個 clone 推一個 commit 到 bare remote，讓原 repo 落後（本機尚未 fetch）。"""
    other = tmp_path / f"other-{tag}"
    subprocess.run(["git", "clone", "--branch", "main", str(remote), str(other)], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(other, "config", "user.name", "Other")
    _git(other, "config", "user.email", "other@example.invalid")
    (other / f"{tag}.txt").write_text(tag, encoding="utf-8")
    _git(other, "add", f"{tag}.txt")
    _git(other, "commit", "-m", f"remote {tag}")
    _git(other, "push", "origin", "main")


@pytest.fixture()
def fleet(tmp_path: Path):
    """三個 repo：behind（clean）、behind 但 dirty、沒有 remote。"""
    root = tmp_path / "roots"
    behind = root / "behind"
    dirty = root / "dirty"
    local_only = root / "local_only"
    remotes = {"behind": tmp_path / "behind.git", "dirty": tmp_path / "dirty.git"}
    _seed_repo(behind, remotes["behind"], "behind")
    _seed_repo(dirty, remotes["dirty"], "dirty")
    _seed_repo(local_only, None, "local")
    _advance_remote(tmp_path, remotes["behind"], "b1")
    _advance_remote(tmp_path, remotes["dirty"], "d1")
    (dirty / "README.md").write_text("uncommitted change\n", encoding="utf-8")
    return {"root": root, "behind": behind, "dirty": dirty, "local_only": local_only, "remotes": remotes}


def _by_name(payload: dict) -> dict[str, dict]:
    return {repo["name"]: repo for repo in payload["repositories"]}


# ---- scope=all 與 fetch_all ----


def test_scope_all_lists_every_repo_while_recent_keeps_limit(fleet):
    service = LocalRepositorySync(_Config(fleet["root"]))
    recent = service.list_statuses()
    everything = service.list_statuses(scope="all")

    assert recent["scope"] == "recent" and recent["displayed_count"] == 1
    assert everything["scope"] == "all" and everything["displayed_count"] == 3 == everything["repository_count"]
    assert set(everything["summary"]) >= {"synced", "behind", "ahead", "diverged", "no_upstream", "dirty", "unavailable"}
    assert everything["batch"] == {"fetch_all": True, "pull_ff_only": True, "push": False, "max_repositories": 50}
    repos = _by_name(everything)
    # 尚未 fetch：cached remote-tracking ref 還認為已同步（這正是全覽要誠實呈現的邊界）
    assert repos["behind"]["sync_state"] == "synced"
    assert repos["local_only"]["sync_state"] == "no_upstream"
    assert everything["summary"]["dirty"] == 1
    with pytest.raises(RepositorySyncRejected):
        service.list_statuses(scope="everything")


def test_fetch_all_refreshes_remote_tracking_refs_without_touching_worktree(fleet):
    service = LocalRepositorySync(_Config(fleet["root"]))
    before = (fleet["dirty"] / "README.md").read_text(encoding="utf-8")

    receipt = service.fetch_all()

    assert receipt["counts"] == {"success": 2, "failed": 0, "skipped": 1}
    statuses = {item["repo_name"]: item for item in receipt["results"]}
    assert statuses["local_only"]["status"] == "skipped" and "remote" in statuses["local_only"]["reason"]
    assert receipt["worktree_changed"] is False
    # fetch 之後才看得到落後；worktree 的未提交修改原封不動
    repos = _by_name(service.list_statuses(scope="all"))
    assert repos["behind"]["sync_state"] == "behind" and repos["behind"]["behind"] == 1
    assert repos["behind"]["last_fetch_at"] is not None
    assert repos["dirty"]["sync_state"] == "behind" and repos["dirty"]["clean"] is False
    assert (fleet["dirty"] / "README.md").read_text(encoding="utf-8") == before
    assert not (fleet["behind"] / "b1.txt").exists()


# ---- 批次 pull / push ----


def test_batch_plan_and_pull_only_touch_eligible_repos(fleet):
    service = LocalRepositorySync(_Config(fleet["root"]))
    service.fetch_all()

    plan = service.batch_plan("pull_ff_only")
    eligible = {item["name"] for item in plan["eligible"]}
    excluded = {item["name"]: item["reason"] for item in plan["excluded"]}
    assert eligible == {"behind"}
    assert "dirty" in excluded and "clean" in excluded["dirty"]
    assert "local_only" in excluded

    dirty_id = next(r["repo_id"] for r in service.list_statuses(scope="all")["repositories"] if r["name"] == "dirty")
    receipt = service.batch_execute("pull_ff_only", [item["repo_id"] for item in plan["eligible"]] + [dirty_id, dirty_id])

    assert receipt["requested"] == 2  # 重複 id 去重
    assert receipt["counts"] == {"success": 1, "failed": 0, "skipped": 1}
    by_name = {item["repo_name"]: item for item in receipt["results"]}
    assert by_name["behind"]["status"] == "success" and by_name["behind"]["after_sync_state"] == "synced"
    assert by_name["dirty"]["status"] == "skipped"
    assert (fleet["behind"] / "b1.txt").exists()
    assert not (fleet["dirty"] / "d1.txt").exists()  # dirty repo 完全沒被動到
    assert receipt["force"] is False

    with pytest.raises(RepositorySyncRejected, match="批次清單為空"):
        service.batch_execute("pull_ff_only", [])
    with pytest.raises(RepositorySyncRejected):
        service.batch_execute("commit_staged", ["0" * 16])


def test_batch_push_is_off_by_default_and_pushes_only_ahead_repos_when_enabled(tmp_path: Path):
    root = tmp_path / "roots"
    ahead = root / "ahead"
    remote = tmp_path / "ahead.git"
    _seed_repo(ahead, remote, "ahead")
    (ahead / "local.txt").write_text("local commit\n", encoding="utf-8")
    _git(ahead, "add", "local.txt")
    _git(ahead, "commit", "-m", "local ahead")

    disabled = LocalRepositorySync(_Config(root))
    with pytest.raises(RepositorySyncRejected, match="allow_push"):
        disabled.batch_plan("push")
    with pytest.raises(RepositorySyncRejected, match="allow_push"):
        disabled.batch_execute("push", ["0" * 16])
    assert disabled.list_statuses(scope="all")["batch"]["push"] is False

    enabled = LocalRepositorySync(_Config(root, **{"repository_sync.batch.allow_push": True}))
    plan = enabled.batch_plan("push")
    assert [item["name"] for item in plan["eligible"]] == ["ahead"]
    receipt = enabled.batch_execute("push", [item["repo_id"] for item in plan["eligible"]])
    assert receipt["counts"]["success"] == 1
    assert receipt["results"][0]["after_sync_state"] == "synced"
    remote_log = subprocess.run(["git", "log", "--oneline", "main"], cwd=remote, text=True, stdout=subprocess.PIPE, check=True).stdout
    assert "local ahead" in remote_log


def test_batch_api_schema_is_strict_and_push_returns_409_when_disabled(fleet, monkeypatch):
    service = LocalRepositorySync(_Config(fleet["root"]))
    monkeypatch.setattr("core.server.LocalRepositorySync", lambda: service)
    client = TestClient(app)

    listed = client.get("/api/v1/repos/sync-status?scope=all")
    assert listed.status_code == 200 and listed.json()["displayed_count"] == 3
    assert client.get("/api/v1/repos/sync-status?scope=bogus").status_code == 422

    fetched = client.post("/api/v1/repos/sync-fetch-all", json={"confirmation": "confirmed"})
    assert fetched.status_code == 200 and fetched.json()["counts"]["success"] == 2
    assert client.post("/api/v1/repos/sync-fetch-all", json={}).status_code == 422

    plan = client.get("/api/v1/repos/sync-batch-plan?action=pull_ff_only")
    assert plan.status_code == 200 and plan.json()["eligible_count"] == 1
    assert client.get("/api/v1/repos/sync-batch-plan?action=push").status_code == 409
    assert client.get("/api/v1/repos/sync-batch-plan?action=commit_staged").status_code == 422

    repo_id = plan.json()["eligible"][0]["repo_id"]
    assert client.post("/api/v1/repos/sync-batch", json={
        "action": "pull_ff_only", "repo_ids": [repo_id], "confirmation": "confirmed", "path": "C:/x",
    }).status_code == 422
    assert client.post("/api/v1/repos/sync-batch", json={
        "action": "pull_ff_only", "repo_ids": ["../etc"], "confirmation": "confirmed",
    }).status_code == 422
    assert client.post("/api/v1/repos/sync-batch", json={
        "action": "push", "repo_ids": [repo_id], "confirmation": "confirmed",
    }).status_code == 409
    ok = client.post("/api/v1/repos/sync-batch", json={
        "action": "pull_ff_only", "repo_ids": [repo_id], "confirmation": "confirmed",
    })
    assert ok.status_code == 200 and ok.json()["counts"]["success"] == 1


# ---- 小秘書：L0 報告 → 提案 → L1 批准式 pull ----


class _ReportConfig(_Config):
    def __init__(self, root: Path, reports_dir: Path, **overrides):
        super().__init__(root, **{"exporters.reports_dir": str(reports_dir), **overrides})


def test_repo_sync_report_writes_snapshot_and_is_offline(fleet, tmp_path, monkeypatch):
    cfg = _ReportConfig(fleet["root"], tmp_path / "reports")
    service = LocalRepositorySync(cfg)
    service.fetch_all()
    now = datetime(2026, 9, 2, 8, 30)

    # 報告一律不 fetch：把 fetch_all/execute 換成會爆炸的替身即可證明
    monkeypatch.setattr(service, "fetch_all", lambda: pytest.fail("report must not fetch"))
    monkeypatch.setattr(service, "execute", lambda *a, **k: pytest.fail("report must not execute git writes"))
    receipt = build_repo_sync_report(cfg=cfg, now=now, sync=service)

    assert receipt["repos_scanned"] == 3
    assert receipt["needs_pull"] == 1 and receipt["needs_push"] == 0
    assert receipt["dirty"] == 1 and receipt["no_upstream"] == 1
    assert Path(receipt["output_path"]).name == "RepoSync_20260902.md"
    markdown = Path(receipt["output_path"]).read_text(encoding="utf-8")
    assert "behind" in markdown and "需要 pull" in markdown
    snapshot = load_snapshot(cfg)
    assert snapshot["repository_count"] == 3 and snapshot["remote_tracking_basis"] == "cached_local_remote_tracking_ref"
    assert {r["name"] for r in snapshot["repositories"]} == {"behind", "dirty", "local_only"}

    signals, meta = collect_repo_sync_signals(cfg=cfg, now=now + timedelta(hours=1))
    assert meta["used"] is True
    assert [s["signal_type"] for s in signals] == ["repo_needs_pull"]
    assert signals[0]["project_key"] == "behind" and signals[0]["subject_ref"].startswith("repo:")

    stale, stale_meta = collect_repo_sync_signals(cfg=cfg, now=now + timedelta(days=3))
    assert stale == [] and stale_meta["reason"] == "snapshot_stale"


def test_report_template_is_registered_as_l0_only():
    template = SCHEDULABLE_TEMPLATES["repo_sync_report"]
    assert template.risk_level == RISK_L0
    assert "needs_pull" in template.receipt_fields and "output_path" in template.receipt_fields


@dataclass(frozen=True)
class _Ref:
    repo_id: str
    path: Path


class _TempDatabase:
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


class _Cfg:
    def get(self, key, default=None):
        return {"proactive_secretary.executor.enabled": True}.get(key, default)


def _services():
    calls = []

    def repo_execute(repo_id, action):
        calls.append((repo_id, action))
        return {"repo_name": "behind", "action": action, "status": "success", "return_code": 0,
                "before": {"secret": "x"}, "after": {"secret": "x"}}

    services = ExecutorServices(
        repo_references=lambda: [_Ref("a" * 16, Path("/tmp/behind"))],
        repo_execute=repo_execute,
        build_handoff=lambda key: {"project": key},
        format_handoff=lambda data: "# h",
        loop_transition=lambda *a, **k: {},
    )
    return services, calls


def _proposal(kind, repo_id="a" * 16):
    return {"proposal_id": f"{kind}-1", "proposal_type": kind, "project_key": "behind",
            "subject_ref": f"repo:{repo_id}", "evidence_refs": [f"repo_sync_snapshot:{repo_id}"]}


def test_executor_maps_repo_sync_proposals_to_l1_templates():
    services, _ = _services()
    pull = derive_action(_proposal("repo_needs_pull"), services=services)
    assert pull.template_id == "repo_pull_ff" and pull.risk_level == RISK_L1
    assert pull.params == {"repo_id": "a" * 16, "action": "pull_ff_only"}
    push = derive_action(_proposal("repo_needs_push"), services=services)
    assert push.template_id == "repo_fetch" and push.risk_level == RISK_L1  # push 不代辦
    assert derive_action(_proposal("repo_needs_pull", repo_id="b" * 16), services=services) is None  # 不在探索範圍
    assert derive_action({**_proposal("repo_needs_pull"), "subject_ref": "repo:../x"}, services=services) is None
    diverged = derive_action(_proposal("repo_diverged"), services=services)  # 分歧只提醒：不得對應任何 Git 寫入
    assert diverged is None or diverged.template_id == "generate_handoff"


def test_approved_pull_executes_through_repo_sync_and_leaves_receipt():
    database = _TempDatabase()
    services, calls = _services()
    proposal = _proposal("repo_needs_pull")
    response = execute_proposal(
        proposal["proposal_id"], database=database, cfg=_Cfg(), services=services,
        proposal_lookup=lambda pid, **_: proposal if pid == proposal["proposal_id"] else None,
        now=datetime(2026, 9, 2, 9, 0), approved_via="telegram_inline",
    )
    assert calls == [("a" * 16, "pull_ff_only")]
    receipt = response["receipt"]
    assert receipt["template_id"] == "repo_pull_ff" and receipt["status"] == "succeeded"
    assert receipt["approved_via"] == "telegram_inline"
    summary = json.loads(receipt["output_summary"])
    assert set(summary) == {"repo_name", "action", "status", "return_code"}
    with database.session_scope() as session:
        assert session.query(AgentExecutionReceipt).count() == 1
