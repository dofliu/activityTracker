"""驗收中心的契約（docs/TODO.md A 段的收據查詢）。

- 每一項都要說清楚：怎麼做、完成判準是什麼、現在查到什麼。
- 只讀。跑完一份報告不得在任何資料表留下一列，也不得 import subprocess／HTTP 客戶端。
- 查得到收據才 passed；查不到就如實說查不到，不因為「功能有寫」而給綠燈。
- 記憶體狀態（檢索 worker）在 CLI 另一個程序查不到，回 runtime_only 而不是 pending。
- 人工署名（attested）只能讓機器沒有判準可查的項目收斂，**永遠不覆蓋機器判定**。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from core import acceptance
from core.acceptance import (
    ATTESTED,
    ITEM_IDS,
    NEEDS_HUMAN,
    NOT_CONFIGURED,
    PARTIAL,
    PASSED,
    PENDING,
    RUNTIME_ONLY,
    build_acceptance_report,
    load_confirmations,
    record_human_confirmation,
)
from core.models import (
    AgentExecutionReceipt,
    Base,
    CalendarEvent,
    CoverageLedgerInterval,
    RAGChatMessage,
    RAGIndexedFile,
    RAGIndexJob,
    SecretaryNote,
)
from core.server import app

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 4, 14, 0)


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


@pytest.fixture
def cfg(tmp_path):
    return DictConfig({"exporters": {"reports_dir": str(tmp_path / "reports")}})


@pytest.fixture
def db():
    return TempDatabase()


def _report(db, cfg, **kwargs):
    return build_acceptance_report(database=db, cfg=cfg, now=NOW, **kwargs)


def _item(report, item_id):
    return next(item for item in report["items"] if item["id"] == item_id)


def _add(db, *rows):
    with db.session_scope() as session:
        for row in rows:
            session.add(row)


def _receipt(template_id, status="succeeded", approved_via="web_click", **extra):
    return AgentExecutionReceipt(
        proposal_id=extra.pop("proposal_id", "p1"),
        template_id=template_id,
        risk_level=extra.pop("risk_level", "l0"),
        action_call="call",
        status=status,
        approved_via=approved_via,
        requested_at=NOW - timedelta(hours=1),
        **extra,
    )


def _full_day_ledger(db, day_offset=1):
    """前一天整天都在觀測：一段從 00:00 到 24:00 的 interval。"""
    start = datetime.combine((NOW - timedelta(days=day_offset)).date(), datetime.min.time())
    _add(db, CoverageLedgerInterval(
        collector="window_watcher",
        started_at=start,
        last_heartbeat_at=start + timedelta(days=1),
        heartbeat_count=288,
        closed_at=start + timedelta(days=1),
    ))


# ---- 形狀與邊界 ----


def test_every_item_says_how_and_what_counts_as_done(db, cfg):
    report = _report(db, cfg)
    assert [item["id"] for item in report["items"]] == list(ITEM_IDS)
    for item in report["items"]:
        assert item["title"] and item["how"] and item["criterion"] and item["detail"]
        assert item["status"] in {
            PASSED, PARTIAL, PENDING, NEEDS_HUMAN, NOT_CONFIGURED, RUNTIME_ONLY, ATTESTED
        }
        assert isinstance(item["evidence"], dict)
    assert "只讀" in report["claim_boundary"]
    assert report["source"] == "docs/TODO.md A 段"


def test_report_is_read_only_and_leaves_no_row_behind(db, cfg):
    _add(db, SecretaryNote(kind="user_note", body="x", source="web"))
    with db.session_scope() as session:
        before = {
            table.name: session.execute(func.count().select().select_from(table)).scalar()
            for table in Base.metadata.sorted_tables
        }
    _report(db, cfg)
    with db.session_scope() as session:
        after = {
            table.name: session.execute(func.count().select().select_from(table)).scalar()
            for table in Base.metadata.sorted_tables
        }
    assert before == after


def test_module_never_shells_out_or_goes_online():
    source = (acceptance.__file__ or "")
    text = open(source, encoding="utf-8").read()
    for forbidden in ("import subprocess", "import requests", "import httpx", "urllib.request"):
        assert forbidden not in text, f"驗收中心不得 {forbidden}"


# ---- A1 coverage ledger ----


def test_a1_pending_without_any_interval(db, cfg):
    item = _item(_report(db, cfg), "A1")
    assert item["status"] == PENDING
    assert item["evidence"]["completed_days_with_ledger"] == 0
    assert item["blocks_release"] is True


def test_a1_passes_on_a_full_day_ledger(db, cfg):
    _full_day_ledger(db)                      # 昨天整天（已結束的一天）
    item = _item(_report(db, cfg), "A1")
    assert item["status"] == PASSED
    assert item["evidence"]["best_completed_day"]["meets_full_coverage"] is True


def test_a1_never_passes_on_todays_partial_day(db, cfg):
    """使用者實機回報 97.12% 通過，但那是「今天到現在」的比例——不是全天。

    get_daily_coverage 對當天的分母只到現在，所以早上跑幾小時就能到 97%。
    A1 的判準是跨午夜的完整一天，今天永遠只能當進度。
    """
    morning = datetime(2026, 9, 5, 3, 0)          # 今天才過了 3 小時
    start = datetime.combine(morning.date(), datetime.min.time())
    _add(db, CoverageLedgerInterval(
        collector="window_watcher", started_at=start,
        last_heartbeat_at=start + timedelta(hours=2, minutes=55),
        heartbeat_count=35,
    ))
    report = build_acceptance_report(database=db, cfg=cfg, now=morning)
    item = _item(report, "A1")

    assert item["status"] == PENDING
    assert item["evidence"]["today_partial"]["coverage_ratio"] > 0.95   # 今天確實很高
    assert item["evidence"]["completed_days_with_ledger"] == 0          # 但沒有任何完整日
    assert "不算全天" in item["detail"]
    assert "A1" in report["summary"]["blocking_release"]


def test_a1_reports_the_best_day_when_below_threshold(db, cfg):
    start = datetime.combine((NOW - timedelta(days=1)).date(), datetime.min.time())
    _add(db, CoverageLedgerInterval(
        collector="window_watcher",
        started_at=start,
        last_heartbeat_at=start + timedelta(hours=6),
        heartbeat_count=72,
        closed_at=start + timedelta(hours=6),
    ))
    item = _item(_report(db, cfg), "A1")
    assert item["status"] == PENDING
    assert item["evidence"]["best_completed_day"]["coverage_ratio"] == pytest.approx(0.25, abs=0.01)
    assert item["evidence"]["completed_days_with_ledger"] == 1


# ---- A2 雲端 provider ----


def test_a2_passes_on_a_real_cloud_answer(db, cfg):
    _add(db, RAGChatMessage(session_id="s", role="assistant", content="這是真的回答",
                            provider="Gemini", model="gemini-2.0", created_at=NOW))
    item = _item(_report(db, cfg), "A2")
    assert item["status"] == PASSED
    assert item["evidence"]["latest_ok"]["provider"] == "Gemini"


def test_a2_does_not_count_error_strings_or_local_ollama(db, cfg):
    _add(
        db,
        RAGChatMessage(session_id="s", role="assistant", content="【尚未偵測到 OpenAI API Key，請設定】",
                       provider="openai", created_at=NOW),
        RAGChatMessage(session_id="s", role="assistant", content="本機回答", provider="ollama", created_at=NOW),
    )
    item = _item(_report(db, cfg), "A2")
    assert item["status"] == PARTIAL
    assert item["evidence"]["cloud_replies"] == 0 and item["evidence"]["cloud_error_replies"] == 1


# ---- A3／A4 危險能力：關著不算失敗 ----


def test_a3_default_off_is_not_configured_not_a_failure(db, cfg):
    item = _item(_report(db, cfg), "A3")
    assert item["status"] == NOT_CONFIGURED
    assert item["evidence"]["approvals_enabled"] is False


def test_a3_passes_on_a_telegram_inline_receipt(db, cfg):
    _add(db, _receipt("repo_pull_ff", approved_via="telegram_inline", risk_level="l1"))
    item = _item(_report(db, cfg), "A3")
    assert item["status"] == PASSED and item["evidence"]["succeeded"] == 1


def test_a4_partial_when_draft_receipts_all_failed(db, cfg):
    _add(db, _receipt("agent_draft_plan", status="failed", risk_level="l2", error_code="timeout"))
    item = _item(_report(db, cfg), "A4")
    assert item["status"] == PARTIAL
    assert item["evidence"]["latest_draft"]["error_code"] == "timeout"


# ---- A5：沒有收據就不假裝有 ----


def test_a5_stays_human_because_onboarding_leaves_no_receipt(db, cfg):
    item = _item(_report(db, cfg), "A5")
    assert item["status"] == NEEDS_HUMAN
    assert item["evidence"]["receipt_available"] is False


# ---- A6：記憶體狀態不能在 CLI 假裝查得到 ----


def test_a6_is_runtime_only_outside_the_service_process(db, cfg):
    item = _item(_report(db, cfg), "A6")
    assert item["status"] == RUNTIME_ONLY
    assert item["evidence"]["basis"] == "in_memory_process_state"


def test_a6_says_the_index_is_empty_instead_of_telling_you_to_wait(db, cfg, monkeypatch):
    """index_present() 只看檔案在不在，不看 chunk 數。所以「索引目錄在但是空的」會讓
    worker 預熱完成卻仍是 0 chunk——此時叫使用者「等預熱」是指錯方向（2026-09-07 實機）。"""
    import rag.retrieval_client as rc

    warm_but_empty = {
        "mode": "worker", "state": "ready", "index_present": True, "warmup_at": "2026-09-06T13:47:09",
        "warmup": {"bm25_chunks": 0, "vector_chunks": 0}, "requests_served": 1,
        "last_retrieval_ms": 6, "last_error": None,
    }
    monkeypatch.setattr(rc.retrieval_client, "status", lambda: warm_but_empty)
    item = _item(_report(db, cfg, runtime=True), "A6")
    assert item["status"] == NOT_CONFIGURED
    assert "0 個 chunk" in item["detail"] and "02 知識庫" in item["detail"]

    # 還沒預熱過（沒有 warmup_at）才是真的「等預熱」
    cold = {**warm_but_empty, "state": "starting", "warmup_at": None}
    monkeypatch.setattr(rc.retrieval_client, "status", lambda: cold)
    assert _item(_report(db, cfg, runtime=True), "A6")["status"] == PENDING

    # 有內容就照舊 passed
    loaded = {**warm_but_empty, "warmup": {"bm25_chunks": 1200, "vector_chunks": 1200}}
    monkeypatch.setattr(rc.retrieval_client, "status", lambda: loaded)
    passed = _item(_report(db, cfg, runtime=True), "A6")
    assert passed["status"] == PASSED and "bm25=1200" in passed["detail"]


def test_a6_will_not_pass_on_a_stale_load(db, cfg, monkeypatch):
    """索引重建之後，worker 記憶體裡的收據不會自己更新。載入的是舊索引就不該判綠
    （2026-09-07 實機：索引已有 4839 chunk，收據仍是 3，A6 卻說 passed）。"""
    import rag.retrieval_client as rc

    ready = {
        "mode": "worker", "state": "ready", "index_present": True, "warmup_at": "2026-09-07T21:11:55",
        "warmup": {"bm25_chunks": 3, "vector_chunks": 3}, "requests_served": 0,
        "last_retrieval_ms": None, "last_error": None,
    }
    monkeypatch.setattr(rc.retrieval_client, "status", lambda: ready)

    # 索引來源計數遠大於 worker 載入的量 → 舊索引
    with db.session_scope() as session:
        session.add(RAGIndexedFile(
            path="C:/docs/big.md", filename="big.md", extension="md",
            chunk_count=4839, status="indexed",
        ))
    item = _item(_report(db, cfg, runtime=True), "A6")
    assert item["status"] == PARTIAL
    assert "舊索引" in item["detail"] and "4839" in item["detail"]
    assert item["evidence"]["source_chunks"] == 4839

    # 重新預熱、計數對上了才是 passed
    reloaded = {**ready, "warmup": {"bm25_chunks": 4839, "vector_chunks": 4839}}
    monkeypatch.setattr(rc.retrieval_client, "status", lambda: reloaded)
    assert _item(_report(db, cfg, runtime=True), "A6")["status"] == PASSED


# ---- A21：Chroma 空間回收只認 worker 收據 ----


def test_a21_reports_the_reclamation_receipt_or_says_there_is_none(db, cfg):
    """Chroma 的 delete_collection 只做邏輯刪除，磁碟不會變小（ADR-009 Addendum C）。
    這一項不去猜目錄現在多大——沒有收據就說沒跑過。"""
    import json as _json

    pending = _item(_report(db, cfg), "A21")
    assert pending["status"] == PENDING and pending["evidence"]["receipt_available"] is False
    assert "回收 Chroma 空間" in pending["detail"]

    def _job(job_id, status, result):
        return RAGIndexJob(
            id=job_id, job_type="compact_chroma", status=status,
            completed_at=NOW - timedelta(minutes=5),
            result_json=_json.dumps(result, ensure_ascii=False),
        )

    with db.session_scope() as session:
        session.add(_job("j1", "completed", {
            "reclaimed_bytes": 4_100_000_000, "before_bytes": 4_240_000_000,
            "after_bytes": 140_000_000, "removed_dirs": [{"name": "eb2894eb", "bytes": 4_000_000_000}],
            "failed_dirs": [], "vacuum": {"ran": True, "integrity": "ok"},
            "after": {"reclaimable_bytes": 0},
        }))
    passed = _item(_report(db, cfg), "A21")
    assert passed["status"] == PASSED and passed["evidence"]["removed_dirs"] == 1
    assert "4,100,000,000" in passed["detail"]

    # 刪不掉的（檔案被開著）不是綠燈，而且要說出下一步
    with db.session_scope() as session:
        session.query(RAGIndexJob).delete()
        session.add(_job("j2", "completed", {
            "reclaimed_bytes": 0, "before_bytes": 10, "after_bytes": 10, "removed_dirs": [],
            "failed_dirs": [{"name": "eb2894eb", "error": "PermissionError: in use"}],
            "vacuum": {"ran": False, "error": "OperationalError: database is locked"},
        }))
    blocked = _item(_report(db, cfg), "A21")
    assert blocked["status"] == PARTIAL and "釋放記憶體" in blocked["detail"]


def test_a21_says_the_reclamation_is_still_running_instead_of_unfinished(db, cfg):
    """工作還在跑不是「沒有完成」。4 GB 的 VACUUM 要一兩分鐘，這時候叫使用者
    「再跑一次」是指錯方向——而且同時只能有一個索引工作，再按也會被拒（2026-09-08 實機）。"""
    with db.session_scope() as session:
        session.add(RAGIndexJob(
            id="j-run", job_type="compact_chroma", status="running",
            requested_at=NOW - timedelta(seconds=40), completed_at=None, result_json=None,
        ))
    item = _item(_report(db, cfg), "A21")
    assert item["status"] == PENDING
    assert "進行中" in item["detail"] and "再跑一次" not in item["detail"]
    assert item["evidence"]["job_status"] == "running"

    # 進行中的工作不會蓋掉先前那份成功的收據
    import json as _json

    with db.session_scope() as session:
        session.add(RAGIndexJob(
            id="j-old", job_type="compact_chroma", status="completed",
            completed_at=NOW - timedelta(hours=2),
            result_json=_json.dumps({
                "reclaimed_bytes": 4_100_000_000, "before_bytes": 4_240_000_000,
                "after_bytes": 140_000_000, "removed_dirs": [{"name": "eb2894eb", "bytes": 1}],
                "failed_dirs": [], "vacuum": {"ran": True, "integrity": "ok"},
            }),
        ))
    assert _item(_report(db, cfg), "A21")["status"] == PENDING   # 進行中優先
    with db.session_scope() as session:
        session.query(RAGIndexJob).filter(RAGIndexJob.id == "j-run").delete()
    assert _item(_report(db, cfg), "A21")["status"] == PASSED


# ---- A7／A8 報告與收據 ----


def test_a7_needs_both_a_report_file_and_an_approved_pull(db, cfg, tmp_path):
    report_dir = tmp_path / "reports" / "repo_sync"
    report_dir.mkdir(parents=True)
    (report_dir / "RepoSync_20260904.md").write_text("# report", encoding="utf-8")
    _add(db, _receipt("repo_sync_report"))
    assert _item(_report(db, cfg), "A7")["status"] == PARTIAL

    _add(db, _receipt("repo_pull_ff", risk_level="l1"))
    item = _item(_report(db, cfg), "A7")
    assert item["status"] == PASSED
    assert item["evidence"]["reports"]["count"] == 1


def test_a8_needs_both_morning_pack_and_handoff(db, cfg):
    _add(db, _receipt("morning_pack"))
    assert _item(_report(db, cfg), "A8")["status"] == PARTIAL
    _add(db, _receipt("handoff_active_projects"))
    assert _item(_report(db, cfg), "A8")["status"] == PASSED


# ---- A9／A10 記憶區 ----


def test_a9_lists_what_is_still_missing(db, cfg):
    _add(db, SecretaryNote(kind="user_note", body="記一下", source="web"))
    item = _item(_report(db, cfg), "A9")
    assert item["status"] == PARTIAL
    assert "preference" in item["detail"] and "observation" in item["detail"]


def test_a9_passes_when_all_three_kinds_exist(db, cfg):
    _add(
        db,
        SecretaryNote(kind="user_note", body="a", source="web"),
        SecretaryNote(kind="preference", body="b", source="web"),
        SecretaryNote(kind="observation", body="c", source="morning_pack"),
    )
    assert _item(_report(db, cfg), "A9")["status"] == PASSED


def test_a10_reads_the_note_that_came_from_the_phone(db, cfg):
    _add(db, SecretaryNote(kind="user_note", body="從手機記的", source="telegram"))
    item = _item(_report(db, cfg), "A10")
    assert item["status"] == PASSED and item["evidence"]["notes_from_telegram"] == 1


# ---- A13 行事曆 ----


def test_a13_without_paths_is_not_configured(db, cfg):
    assert _item(_report(db, cfg), "A13")["status"] == NOT_CONFIGURED


def test_a13_passes_once_events_landed(db, cfg, tmp_path):
    cfg.data["watchers"] = {"calendar_watcher": {"enabled": True, "paths": [str(tmp_path / "a.ics")]}}
    _add(db, CalendarEvent(uid="u1", instance_start=NOW, instance_end=NOW + timedelta(hours=1),
                           summary="會議", source_path=str(tmp_path / "a.ics"), last_seen_at=NOW))
    item = _item(_report(db, cfg), "A13")
    assert item["status"] == PASSED
    assert item["evidence"]["events"] == 1 and item["evidence"]["source_files"] == 1


# ---- 人工署名 ----


def test_human_confirmation_settles_only_items_the_machine_cannot_judge(db, cfg):
    record_human_confirmation("A12", note="數字已對照 03/04", cfg=cfg, now=NOW)
    item = _item(_report(db, cfg), "A12")
    assert item["status"] == ATTESTED
    assert item["attestation"]["note"] == "數字已對照 03/04"
    assert item["attestation"]["basis"] == "human_attested_not_machine_evidence"


def test_human_confirmation_never_overrides_a_machine_verdict(db, cfg):
    record_human_confirmation("A1", note="我說可以就可以", cfg=cfg, now=NOW)
    item = _item(_report(db, cfg), "A1")
    assert item["status"] == PENDING          # 機器查不到 ledger，署名不能變綠
    assert item["attestation"] is not None    # 但署名本身仍如實留著
    assert "A1" in _report(db, cfg)["summary"]["blocking_release"]


def test_confirmation_can_be_taken_back(db, cfg):
    record_human_confirmation("A11", cfg=cfg, now=NOW)
    assert "A11" in load_confirmations(cfg)
    record_human_confirmation("A11", confirmed=False, cfg=cfg, now=NOW)
    assert load_confirmations(cfg) == {}


def test_unknown_item_is_rejected(cfg):
    with pytest.raises(ValueError):
        record_human_confirmation("A99", cfg=cfg)


def test_corrupt_confirmation_file_is_ignored_not_fatal(db, cfg):
    path = acceptance.confirmations_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_confirmations(cfg) == {}
    assert len(_report(db, cfg)["items"]) == len(ITEM_IDS)


# ---- 彙總與 gate ----


def test_one_broken_probe_does_not_break_the_page(db, cfg, monkeypatch):
    def boom(_ctx):
        raise RuntimeError("probe exploded")

    patched = tuple(
        {**spec, "probe": boom} if spec["id"] == "A9" else spec for spec in acceptance._ITEMS
    )
    monkeypatch.setattr(acceptance, "_ITEMS", patched)
    report = _report(db, cfg)
    assert len(report["items"]) == len(ITEM_IDS)
    assert _item(report, "A9")["evidence"]["probe_error"] == "RuntimeError"


def test_partial_run_withholds_release_gates(db, cfg):
    report = _report(db, cfg, only=["a1"])
    assert [item["id"] for item in report["items"]] == ["A1"]
    assert report["release_gates"] == [] and report["release_gates_note"]


def test_gate_g1_closes_only_when_every_p0_item_has_a_receipt(db, cfg):
    _full_day_ledger(db)
    _add(db, RAGChatMessage(session_id="s", role="assistant", content="答案",
                            provider="gemini", created_at=NOW))
    gates = {gate["id"]: gate for gate in _report(db, cfg)["release_gates"]}
    assert gates["G1"]["status"] == PASSED and gates["G1"]["outstanding"] == []


# ---- API ----


def test_checklist_endpoint_returns_the_same_report():
    client = TestClient(app)
    response = client.get("/api/v1/acceptance/checklist", headers={"Origin": _LOCAL_ORIGIN})
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "server"
    assert [item["id"] for item in payload["items"]] == list(ITEM_IDS)
    assert payload["claim_boundary"]


def test_confirm_endpoint_rejects_an_unknown_item():
    client = TestClient(app)
    response = client.post(
        "/api/v1/acceptance/confirm",
        json={"item_id": "A99", "confirmed": True, "note": ""},
        headers={"Origin": _LOCAL_ORIGIN},
    )
    assert response.status_code == 422
