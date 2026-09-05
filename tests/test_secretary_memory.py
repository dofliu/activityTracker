"""小秘書記憶區（ADR-012）的契約。

- 筆記 CRUD：四種 kind、長度上限、observation 依 source_ref 去重、可單筆與整類刪除。
- 對話前綴：「記下來／偏好／決定」與英文命令解析成筆記；一般提問回 None。
- 早晨包收據 → 當日觀察（每天每項一則），且記憶區故障不會讓早晨包失敗。
- 提案引擎：偏好「不要提醒 X」壓掉提案並如實計數；同專案的決定附在提案卡。
- 對話脈絡：固定順序、字數上限、附收據；關閉開關就完全不注入。
- API：寫入只接受使用者可寫的 kind、observation 可一鍵清除、context 端點與注入內容相同。
- RAG：筆記、微摘要與報告檔成為 activity 領域切片，且根目錄只認秘書自己的檔名。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, SecretaryNote
from core.secretary_memory import (
    MAX_BODY_CHARS,
    MemoryRejected,
    add_note,
    clear_notes,
    delete_note,
    list_notes,
    memory_context,
    observations_from_pack,
    parse_note_command,
    preference_mutes,
    project_memory_lines,
    record_observation,
)
from core.server import app

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 2, 9, 0)


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
    def __init__(self, path: Path | None = None):
        # 端點測試要用檔案型 SQLite：TestClient 在其他執行緒跑同步端點，in-memory 連線不共享。
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


def _cfg(**memory):
    return DictConfig({"secretary_memory": memory, "exporters": {"reports_dir": "/nonexistent"}})


# ---- CRUD ----


def test_add_list_delete_and_kind_validation():
    db = TempDatabase()
    note = add_note(kind="user_note", body="  週五前把 ADR-012 收尾  ", project_key="OmniContext", database=db, now=NOW)
    assert note["body"] == "週五前把 ADR-012 收尾" and note["kind_label"] == "筆記" and note["deletable"] is True
    listed = list_notes(database=db)
    assert listed["total"] == 1 and listed["counts"]["user_note"] == 1 and listed["notes"][0]["id"] == note["id"]
    with pytest.raises(MemoryRejected) as exc:
        add_note(kind="rumour", body="x", database=db)
    assert exc.value.error_code == "invalid_kind"
    with pytest.raises(MemoryRejected):
        add_note(kind="user_note", body="   ", database=db)
    with pytest.raises(MemoryRejected) as exc:
        add_note(kind="user_note", body="x" * (MAX_BODY_CHARS + 1), database=db)
    assert exc.value.error_code == "body_too_long"
    assert delete_note(note["id"], database=db)["deleted"] is True
    assert delete_note(note["id"], database=db) == {"deleted": False, "id": note["id"], "reason": "not_found"}


def test_observations_dedupe_by_source_ref_and_clear_leaves_user_notes():
    db = TempDatabase()
    add_note(kind="decision", body="保留 SQLite", database=db, now=NOW)
    first = record_observation(title="t", body="b", source_ref="morning_pack:2026-09-02:repo_sync", database=db, now=NOW)
    again = record_observation(title="t", body="b2", source_ref="morning_pack:2026-09-02:repo_sync", database=db, now=NOW)
    assert first is not None and again is None
    filtered = list_notes(kind="observation", database=db)
    assert len(filtered["notes"]) == 1 and filtered["counts"]["observation"] == 1 and filtered["total"] == 2
    assert clear_notes(kind="observation", database=db) == {"deleted": 1, "kind": "observation"}
    assert list_notes(database=db)["counts"] == {"user_note": 0, "preference": 0, "decision": 1, "observation": 0}


# ---- 對話前綴 ----


@pytest.mark.parametrize(
    "text, kind, body, project",
    [
        ("記下來：明天先修 CI", "user_note", "明天先修 CI", None),
        ("記住 @OmniContext：release 前要跑 Playwright", "user_note", "release 前要跑 Playwright", "OmniContext"),
        ("/note [my-app] use pnpm", "user_note", "use pnpm", "my-app"),
        ("偏好：不要提醒 repo_needs_push", "preference", "不要提醒 repo_needs_push", None),
        ("決定 用 SQLite 不換 Postgres", "decision", "用 SQLite 不換 Postgres", None),
        ("remember: call the vendor", "user_note", "call the vendor", None),
    ],
)
def test_parse_note_command(text, kind, body, project):
    assert parse_note_command(text) == {"kind": kind, "body": body, "project_key": project}


def test_ordinary_questions_are_not_commands():
    assert parse_note_command("目前哪個專案最需要注意？") is None
    assert parse_note_command("記下來：") is None
    assert parse_note_command("") is None


# ---- 早晨包 → 觀察 ----


def test_observations_from_pack_write_once_per_day_and_respect_switch():
    db = TempDatabase()
    receipt = {"repos_scanned": 12, "needs_pull": 2, "needs_push": 0, "diverged": 1, "stale_status": 3, "errors": ["handoff_active_projects: OSError"]}
    written = observations_from_pack(receipt, database=db, now=NOW, cfg=_cfg(enabled=True))
    assert [n["title"] for n in written] == ["2026-09-02 repo 同步狀態", "2026-09-02 STATUS 過期", "2026-09-02 早晨包有步驟失敗"]
    assert all(n["kind"] == "observation" and n["source"] == "morning_pack" for n in written)
    assert observations_from_pack(receipt, database=db, now=NOW + timedelta(hours=2), cfg=_cfg(enabled=True)) == []
    assert observations_from_pack(receipt, database=db, now=NOW + timedelta(days=1), cfg=_cfg(enabled=False)) == []
    assert observations_from_pack({"needs_pull": 0, "errors": []}, database=db, now=NOW + timedelta(days=2), cfg=_cfg(enabled=True)) == []


def test_morning_pack_survives_memory_failure(monkeypatch, tmp_path):
    from core import secretary_packs

    class BrokenDB:
        @contextmanager
        def session_scope(self):
            raise RuntimeError("db locked")
            yield  # pragma: no cover

    cfg = DictConfig({"exporters": {"reports_dir": str(tmp_path)}, "secretary_memory": {"enabled": True}})
    receipt = secretary_packs.build_morning_pack(
        cfg=cfg, now=NOW,
        repo_sync=lambda: {"repos_scanned": 1, "needs_pull": 1, "needs_push": 0, "diverged": 0},
        status_draft=lambda: {"stale_count": 0},
        handoffs=lambda: {"handoffs_written": 0},
        database=BrokenDB(),
    )
    # 記憶層壞掉不得讓早晨包失敗，也不得讓觀察寫入把例外往外丟。
    assert receipt["needs_pull"] == 1 and receipt["observations_written"] == 0
    # 每日工作誌是「讀資料」的步驟，資料庫真的壞掉時它如實記錯（其他步驟照跑），
    # 使用者才知道那天的工作誌沒寫成——這與「記憶層失敗要靜默」是兩件事。
    assert receipt["errors"] == ["daily_digest: RuntimeError"]
    assert receipt.get("digest_notes_written") is None


# ---- 提案引擎讀偏好 ----


def test_preference_mutes_and_project_memory_lines():
    db = TempDatabase()
    add_note(kind="preference", body="不要提醒 repo_needs_push\nmute: legacy-app", database=db, now=NOW)
    add_note(kind="preference", body="早上不要吵我", database=db, now=NOW)  # 純文字，不是 mute
    add_note(kind="decision", body="alpha 先不升級 FastAPI", project_key="alpha", database=db, now=NOW)
    add_note(kind="user_note", body="舊的", project_key="alpha", database=db, now=NOW - timedelta(days=3))
    assert preference_mutes(database=db) == {"repo_needs_push", "legacy-app"}
    lines = project_memory_lines(database=db)
    assert list(lines) == ["alpha"] and lines["alpha"] == ["決定 09-02：alpha 先不升級 FastAPI"]


def test_build_action_proposals_applies_mutes_and_attaches_memory(monkeypatch):
    import core.proactive_secretary as ps

    db = TempDatabase()
    add_note(kind="preference", body="不要提醒 legacy-app", database=db, now=NOW)
    add_note(kind="decision", body="alpha 等 v2 再 merge", project_key="alpha", database=db, now=NOW)
    signals = [
        {"signal_type": "aging_pr", "project_key": "alpha", "subject_ref": "pr:alpha#1", "evidence_ref": "github_pr_events:1",
         "observed_at": NOW, "url": None, "age_days": 4.0, "open_loop_refs": [], "title": "PR 1", "detail": "", "reasons": ["old"], "score": 0.5},
        {"signal_type": "aging_pr", "project_key": "legacy-app", "subject_ref": "pr:legacy#2", "evidence_ref": "github_pr_events:2",
         "observed_at": NOW, "url": None, "age_days": 9.0, "open_loop_refs": [], "title": "PR 2", "detail": "", "reasons": ["old"], "score": 0.7},
    ]
    monkeypatch.setattr(ps, "collect_pr_signals", lambda session, now: signals)
    monkeypatch.setattr(ps, "collect_issue_signals", lambda session, now: [])
    monkeypatch.setattr(ps, "collect_open_loop_signals", lambda session, now, hours: [])
    monkeypatch.setattr(ps, "repo_issue_backlog", lambda session: {})
    monkeypatch.setattr(ps, "build_extension_status", lambda **kw: {"extension": {}})
    cfg = DictConfig({"proactive_secretary": {"enabled": True}, "secretary_memory": {"enabled": True}, "exporters": {"reports_dir": "/nonexistent"}})
    result = ps.build_action_proposals(database=db, cfg=cfg, now=NOW)
    assert [p["project_key"] for p in result["proposals"]] == ["alpha"]
    assert result["proposals"][0]["memory_note"] == "決定 09-02：alpha 等 v2 再 merge"
    assert result["inputs"]["memory_muted"] == 1
    assert result["execution_available"] is False and result["query_persisted"] is False


# ---- 對話脈絡 ----


def test_memory_context_is_ordered_capped_and_receipted():
    db = TempDatabase()
    add_note(kind="preference", body="回答用繁體中文", database=db, now=NOW)
    add_note(kind="user_note", body="週五 demo", project_key="alpha", database=db, now=NOW)
    record_observation(title="舊觀察", body="超過 TTL 的觀察", source_ref="old", database=db, now=NOW - timedelta(days=30))
    record_observation(title="新觀察", body="2 個 repo 需要 pull", source_ref="new", database=db, now=NOW - timedelta(hours=1))
    today = {"resume": {"display_name": "alpha", "last_activity_at": "2026-09-02T08:00:00", "last_action_summary": "修 CI"},
             "pack_line": "早晨包：repo 需 pull 2、需 push 0", "active_project_count": 3}
    proposals = [{"project_key": "alpha", "title": "PR 1 等 review", "why_now": "只差一個 merge"}]
    out = memory_context(database=db, cfg=_cfg(enabled=True), now=NOW, today=today, proposals=proposals)
    text, receipt = out["text"], out["receipt"]
    assert receipt["included"] is True and receipt["sections"] == ["resume", "pack", "proposals", "notes"]
    assert receipt["notes_used"] == 3 and receipt["truncated"] is False and receipt["chars"] == len(text)
    order = [text.index(s) for s in ("上次做到哪：alpha", "早晨包：", "目前建議", "偏好與決定：", "使用者筆記：", "秘書觀察（可刪除）：")]
    assert order == sorted(order)
    assert "超過 TTL 的觀察" not in text and "2 個 repo 需要 pull" in text
    assert "[alpha] 週五 demo" in text

    capped = memory_context(database=db, cfg=_cfg(enabled=True), now=NOW, today=today, proposals=proposals, max_chars=200)
    assert capped["receipt"]["truncated"] is True and len(capped["text"]) <= 200

    off = memory_context(database=db, cfg=_cfg(enabled=False), now=NOW, today=today, proposals=proposals)
    assert off == {"text": "", "receipt": {**off["receipt"]}} and off["receipt"]["included"] is False and off["receipt"]["reason"] == "disabled"


def test_memory_context_without_anything_is_empty_not_noise():
    db = TempDatabase()
    out = memory_context(database=db, cfg=_cfg(enabled=True), now=NOW, today={}, proposals=[])
    assert out["text"] == "" and out["receipt"]["included"] is False


# ---- API ----


def test_memory_endpoints(monkeypatch, tmp_path):
    import core.secretary_memory as mem

    db = TempDatabase(tmp_path / "memory.db")
    monkeypatch.setattr(mem, "get_db", lambda: db)
    client = TestClient(app)
    headers = {"Origin": _LOCAL_ORIGIN}

    res = client.post("/api/v1/secretary/memory", json={"kind": "user_note", "body": "記住這個", "project_key": "alpha"}, headers=headers)
    assert res.status_code == 200 and res.json()["source"] == "web"
    note_id = res.json()["id"]
    # observation 不可由外部寫入；由秘書自己的 L0 收據產生
    res = client.post("/api/v1/secretary/memory", json={"kind": "observation", "body": "x"}, headers=headers)
    assert res.status_code == 422 and res.json()["detail"] == "kind_not_user_writable"
    res = client.post("/api/v1/secretary/memory", json={"kind": "user_note", "body": "   "}, headers=headers)
    assert res.status_code == 422 and res.json()["detail"] == "empty_body"

    record_observation(title="obs", body="早晨包觀察", source_ref="morning_pack:2026-09-02:repo_sync", database=db, now=NOW)
    res = client.get("/api/v1/secretary/memory", headers=headers)
    body = res.json()
    assert res.status_code == 200 and body["total"] == 2 and body["counts"]["observation"] == 1
    assert all(n["deletable"] for n in body["notes"]) and "claim_boundary" in body
    assert client.get("/api/v1/secretary/memory?kind=nope", headers=headers).status_code == 422

    res = client.get("/api/v1/secretary/memory/context", headers=headers)
    assert res.status_code == 200 and "text" in res.json() and "receipt" in res.json()

    res = client.delete("/api/v1/secretary/memory?kind=observation", headers=headers)
    assert res.status_code == 200 and res.json()["deleted"] == 1
    res = client.delete(f"/api/v1/secretary/memory/{note_id}", headers=headers)
    assert res.status_code == 200 and res.json()["deleted"] is True
    assert client.delete(f"/api/v1/secretary/memory/{note_id}", headers=headers).status_code == 404
    assert client.get("/api/v1/secretary/memory", headers=headers).json()["total"] == 0


def test_today_view_reports_memory_counts(tmp_path):
    from core.secretary_packs import build_today_view

    db = TempDatabase()
    add_note(kind="decision", body="d", database=db, now=NOW)
    cfg = DictConfig({"exporters": {"reports_dir": str(tmp_path)}, "proactive_secretary": {"executor": {"enabled": False}}, "secretary_memory": {"enabled": True}})
    view = build_today_view(database=db, cfg=cfg, now=NOW, projects=[])
    assert view["memory"] == {"enabled": True, "counts": {"user_note": 0, "preference": 0, "decision": 1, "observation": 0}, "total": 1}


# ---- RAG 併入 ----


def test_activity_indexer_includes_notes_micro_summaries_and_whitelisted_reports(tmp_path, monkeypatch):
    pytest.importorskip("rank_bm25")
    from core.models import ActivityMicroSummary
    from rag import activity_indexer as ai

    db = TempDatabase()
    add_note(kind="preference", body="回答用繁體中文", database=db, now=NOW)
    record_observation(title="obs", body="2 個 repo 需要 pull", source_ref="x", database=db, now=NOW)
    with db.session_scope() as session:
        session.add(ActivityMicroSummary(period_start=NOW - timedelta(hours=1), period_end=NOW, provider="ollama", summary_text="修了 CI", event_count=3))
    (tmp_path / "handoffs").mkdir()
    (tmp_path / "handoffs" / "Handoff_alpha_20260902_2130.md").write_text("# Handoff alpha\n下一步：merge", encoding="utf-8")
    (tmp_path / "repo_sync").mkdir()
    (tmp_path / "repo_sync" / "RepoSync_20260902.md").write_text("# Repo 同步報告\n", encoding="utf-8")
    (tmp_path / "OMNICONTEXT_TODAY.md").write_text("# 今日\n", encoding="utf-8")
    (tmp_path / "Weekly_Rollup_2026-W35.md").write_text("# 週報\n", encoding="utf-8")
    (tmp_path / "random_user_file.md").write_text("不該被讀", encoding="utf-8")
    (tmp_path / "handoffs" / "big.md").write_text("x" * 7000, encoding="utf-8")
    monkeypatch.setattr(ai, "get_config", lambda: DictConfig({"exporters": {"reports_dir": str(tmp_path)}}))
    monkeypatch.setattr(ai, "resolve_runtime_path", lambda value: Path(value))

    chunks = ai.ActivityIndexer().build_activity_chunks(database=db)
    by_type = {}
    for c in chunks:
        by_type.setdefault(c.metadata["source_type"], []).append(c)
    assert len(by_type["secretary_note"]) == 2
    kinds = {c.metadata["note_kind"]: c.metadata["trust_status"] for c in by_type["secretary_note"]}
    assert kinds == {"preference": "user_stated", "observation": "derived_observation"}
    assert len(by_type["micro_summary"]) == 1 and "修了 CI" in by_type["micro_summary"][0].content
    handoffs = by_type["report_handoff"]
    assert {c.metadata["project_key"] for c in handoffs} == {"alpha", "general"}
    big = next(c for c in handoffs if c.metadata["project_key"] == "general")
    assert big.metadata["truncated"] is True and len(big.content) < 7000
    assert len(by_type["report_repo_sync"]) == 1
    assert len(by_type["report_daily_entry"]) == 1 and len(by_type["report_rollup"]) == 1
    assert not any("random_user_file" in c.file_path for c in chunks)
    assert all(c.metadata["source_domain"] == "activity" for c in chunks)


def test_activity_sync_is_a_registered_worker_job(monkeypatch):
    from rag import jobs as jobs_module
    from rag.router import router  # noqa: F401 — 端點存在

    created = {}

    def fake_create(job_type, folder_id=None, max_files=None, throttle_ms=None):
        created["job_type"] = job_type
        return {"id": "job-1", "job_type": job_type, "status": "queued"}

    import rag.router as router_module

    monkeypatch.setattr(router_module, "create_job", fake_create)
    monkeypatch.setattr(router_module, "launch_worker", lambda job_id: {"id": job_id, "status": "running"})
    client = TestClient(app)
    res = client.post("/api/v1/rag/memory/sync", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and created["job_type"] == "activity_sync" and res.json()["job"]["id"] == "job-1"
    # 未註冊的 job type 仍被拒絕
    with pytest.raises(ValueError):
        jobs_module.create_job("activity_sync_v2")


def test_index_worker_source_dispatches_activity_sync():
    source = Path("rag/index_worker.py").read_text(encoding="utf-8")
    assert 'job["job_type"] == "activity_sync"' in source and "activity_indexer.sync_all()" in source
