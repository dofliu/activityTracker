"""知識庫（DeskRAG）依賴是選用 extra（TODO D1，ROADMAP §13 R0）。

契約：
1. 核心依賴不含任何索引／解析套件；`pip install omnicontext` 不帶 Chroma／fastembed／BM25／jieba。
2. 沒裝 extra 時主服務照常啟動，每一條會用到它們的路徑都**在動手前**說清楚缺什麼：
   建 job → 503 機器可讀 detail；檢索 worker → 不啟動子程序、狀態 `unavailable`；
   對話 → 照常回答只是不帶文件脈絡；啟動預熱 → 略過並寫原因；驗收中心 A6 → not_configured。
3. 這些判定只用 importlib.find_spec，不 import 套件本身。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.server import app
from rag import availability
from rag import retrieval_client as rc_module
from rag.retrieval_client import RetrievalWorkerClient, RetrievalWorkerError

_ORIGIN = {"Origin": "http://127.0.0.1:8765"}
_MISSING = ["chromadb", "fastembed"]


@pytest.fixture
def extra_missing(monkeypatch):
    monkeypatch.setattr(availability, "missing_index_packages", lambda: list(_MISSING))
    return list(_MISSING)


@pytest.fixture
def extra_present(monkeypatch):
    monkeypatch.setattr(availability, "missing_index_packages", lambda: [])


def test_availability_reports_the_pip_names_not_import_names(monkeypatch):
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name in {"rank_bm25", "fitz"} else object())
    assert availability.missing_index_packages() == ["rank-bm25"]
    assert availability.missing_parser_packages() == ["pymupdf"]
    status = availability.rag_extra_status()
    assert status["extra_installed"] is False and status["extra_missing"] == ["rank-bm25"]
    assert status["install_hint"] == 'pip install "omnicontext[rag]"'
    with pytest.raises(availability.RagExtraNotInstalled) as err:
        availability.require_rag_extra()
    assert "rank-bm25" in str(err.value) and 'omnicontext[rag]' in str(err.value)


def test_availability_treats_broken_specs_as_missing(monkeypatch):
    def _boom(name):
        raise ValueError("bad spec")

    monkeypatch.setattr(availability, "find_spec", _boom)
    assert len(availability.missing_index_packages()) == len(availability.INDEX_PACKAGES)


def test_create_job_refuses_before_touching_the_database(extra_missing, monkeypatch):
    from rag import jobs

    def _no_db():
        raise AssertionError("沒裝 extra 就不該開 DB session")

    monkeypatch.setattr(jobs, "get_db", _no_db)
    with pytest.raises(availability.RagExtraNotInstalled):
        jobs.create_job("index", folder_id=1)
    # 型別檢查仍先於依賴檢查：錯的 job type 永遠是 ValueError
    with pytest.raises(ValueError):
        jobs.create_job("nope")


def test_scan_endpoint_returns_machine_readable_503(extra_missing):
    client = TestClient(app)
    res = client.post("/api/v1/rag/scan", json={}, headers=_ORIGIN)
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["error"] == "rag_extra_not_installed"
    assert detail["missing"] == _MISSING
    assert detail["install_hint"] == 'pip install "omnicontext[rag]"'


def test_retrieval_status_says_unavailable_and_warmup_is_refused(extra_missing):
    client = TestClient(app)
    status = client.get("/api/v1/rag/retrieval/status", headers=_ORIGIN).json()
    assert status["extra_installed"] is False
    assert status["extra_missing"] == _MISSING
    assert status["state"] == "unavailable"
    res = client.post("/api/v1/rag/retrieval/warmup", headers=_ORIGIN)
    assert res.status_code == 503 and res.json()["detail"]["error"] == "rag_extra_not_installed"


def test_worker_client_never_spawns_without_the_extra(extra_missing, monkeypatch):
    spawned: list[list[str]] = []

    def _popen(*args, **kwargs):
        spawned.append(list(args[0]))
        raise AssertionError("不得啟動子程序")

    monkeypatch.setattr(rc_module.subprocess, "Popen", _popen)
    client = RetrievalWorkerClient()
    with pytest.raises(RetrievalWorkerError) as err:
        client.retrieve("問題")
    assert "chromadb" in str(err.value) and spawned == []
    assert client.status()["state"] == "unavailable"
    receipt = client.warmup_in_background(reason="test", force=True)
    assert receipt["state"] == "unavailable" and spawned == []
    monkeypatch.setattr(rc_module, "index_present", lambda: True)  # 有索引檔但套件不在（例如換了 venv）
    assert rc_module.maybe_warmup_on_start() == {
        "warmup": "skipped",
        "reason": "rag_extra_not_installed",
        "missing": _MISSING,
    }


def test_chat_still_answers_without_document_context(extra_missing, monkeypatch):
    """對話管線（儀表板與 Telegram 共用）不因少了知識庫依賴而中斷。"""
    import rag.router as router_module

    class _Req:
        retrieval_strategy = None
        top_k = 5
        hybrid_alpha = None
        score_threshold = 0.0

    def _no_retrieve(*a, **k):
        raise AssertionError("沒裝 extra 就不該呼叫 worker")

    monkeypatch.setattr(router_module.retrieval_client, "retrieve", _no_retrieve)
    assert router_module._retrieve_citations("問題", _Req()) == []


def test_acceptance_a6_points_at_the_install_command(extra_missing, monkeypatch, tmp_path):
    import importlib

    from core.acceptance import NOT_CONFIGURED, build_acceptance_report

    fixtures = importlib.import_module("test_acceptance_center")  # 重用既有的記憶體 DB 與設定夾具
    db = fixtures.TempDatabase()
    cfg = fixtures.DictConfig({"exporters": {"reports_dir": str(tmp_path / "reports")}})
    report = build_acceptance_report(database=db, cfg=cfg, now=fixtures.NOW, runtime=True)
    item = next(i for i in report["items"] if i["id"] == "A6")
    assert item["status"] == NOT_CONFIGURED
    assert "chromadb" in item["detail"] and 'omnicontext[rag]' in item["detail"]


def test_status_carries_extra_fields_when_installed(extra_present):
    status = RetrievalWorkerClient().status()
    assert status["extra_installed"] is True and status["extra_missing"] == []
    assert status["state"] == "cold"


def test_injected_worker_command_is_not_gated_by_the_extra(extra_missing):
    """測試與替身 worker 走自訂指令；依賴檢查只針對預設的 `python -m rag.retrieval_worker`。"""
    client = RetrievalWorkerClient(command=["python", "-c", "pass"])
    assert client.status()["state"] == "cold"
    assert client.status()["extra_installed"] is False  # 事實照報，但不阻擋
