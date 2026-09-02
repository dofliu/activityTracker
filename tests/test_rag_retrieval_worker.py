"""常駐檢索 worker 的契約（docs/TODO.md B1 的根因修法）。

主服務不再在自己的程序內載入 Chroma／BM25／embedding；檢索交給
`python -m rag.retrieval_worker` 子程序，以 JSON lines 對話。這裡鎖住：

1. 主服務端 client 的生命週期：lazy 啟動、逾時即 kill 並在下一次重啟、
   worker 崩潰不會讓對話中止、status 收據如實反映重啟次數與錯誤。
2. worker 端協定：request → response 的形狀、錯誤回傳而非死掉、
   stdout 只承載協定訊息。
3. `/api/v1/rag/strategies` 的靜態目錄與 registry 一致（主服務不需 import registry）。
4. 對話串流在 worker 模式下的降級行為與 in_process 模式相同。

測試用的假 worker 不載入任何索引，也不會觸發 embedding 模型下載。
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

from rag import retrieval_client as client_module
from rag.retrieval_client import (
    RetrievalTimeoutError,
    RetrievalWorkerClient,
    RetrievalWorkerError,
)

_LOCAL_ORIGIN = "http://127.0.0.1:8765"


def _fake_worker(tmp_path: Path, behaviour: str) -> list[str]:
    """一個只會說協定的最小 worker；behaviour 決定它對 retrieve 的反應。"""
    script = tmp_path / f"fake_worker_{behaviour}.py"
    script.write_text(textwrap.dedent(f'''
        import json, os, sys, time
        BEHAVIOUR = {behaviour!r}
        print("stray stdout noise that must be ignored")
        sys.stdout.write(json.dumps({{"event": "hello", "pid": os.getpid()}}) + "\\n"); sys.stdout.flush()
        for raw in sys.stdin:
            req = json.loads(raw)
            op = req.get("op")
            if op == "shutdown":
                break
            if op == "warmup":
                out = {{"id": req["id"], "ok": True, "result": {{"bm25_chunks": 3, "vector_chunks": 3,
                        "embedding_ready": True, "durations": {{"total_ms": 12}}, "worker_rss_mb": 42.0}}}}
            elif op == "retrieve":
                if BEHAVIOUR == "hang":
                    time.sleep(30)
                if BEHAVIOUR == "crash":
                    os._exit(3)
                if BEHAVIOUR == "error":
                    out = {{"id": req["id"], "ok": False, "error": {{"type": "OSError", "message": "index unavailable"}}}}
                else:
                    out = {{"id": req["id"], "ok": True, "result": {{"elapsed_ms": 5, "citations": [{{
                        "index": 1, "chunk_id": "c1", "file_path": "C:/docs/a.md", "filename": "a.md",
                        "file_type": "md", "content": "命中 " + req["query"], "score": 0.9,
                        "retrieval_type": "hybrid"}}]}}}}
            else:
                out = {{"id": req.get("id"), "ok": False, "error": {{"type": "ValueError", "message": "bad op"}}}}
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\\n"); sys.stdout.flush()
    '''), encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(behaviour="ok"):
        client = RetrievalWorkerClient(command=_fake_worker(tmp_path, behaviour), cwd=tmp_path)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.shutdown()


# ---- 1. client 生命週期 ----


def test_client_is_lazy_and_returns_citation_payload(make_client):
    client = make_client("ok")
    status = client.status()
    assert status["state"] == "cold" and status["pid"] is None  # 建立 client 不會啟動子程序

    payload = client.retrieve("測試", strategy="hybrid_rrf", top_k=3, timeout=10)
    assert payload[0]["content"] == "命中 測試"
    status = client.status()
    assert status["state"] == "ready" and status["pid"] and status["requests_served"] == 1
    assert status["spawns"] == 1 and status["restarts"] == 0


def test_warmup_receipt_is_kept_and_reused(make_client):
    client = make_client("ok")
    receipt = client.warmup(timeout=10)
    assert receipt["bm25_chunks"] == 3 and receipt["embedding_ready"] is True
    status = client.status()
    assert status["state"] == "ready"
    assert status["warmup"]["vector_chunks"] == 3 and status["warmup_at"]
    # 同一個 worker 繼續服務，不重新啟動
    client.retrieve("q", timeout=10)
    assert client.status()["spawns"] == 1


def test_timeout_kills_worker_and_next_query_restarts(make_client, monkeypatch):
    client = make_client("hang")
    with pytest.raises(RetrievalTimeoutError):
        client.retrieve("慢", timeout=1)
    status = client.status()
    assert status["state"] == "failed" and status["pid"] is None
    assert "timed out" in (status["last_error"] or "")

    # 下一次查詢自動重啟（這個假 worker 仍會卡住，但要證明的是「有重啟」）
    with pytest.raises(RetrievalTimeoutError):
        client.retrieve("再慢", timeout=1)
    assert client.status()["restarts"] == 1 and client.status()["spawns"] == 2


def test_worker_crash_surfaces_as_error_not_hang(make_client):
    client = make_client("crash")
    with pytest.raises(RetrievalWorkerError) as excinfo:
        client.retrieve("boom", timeout=10)
    assert "exited" in str(excinfo.value)
    assert client.status()["state"] == "failed"


def test_worker_side_error_is_propagated(make_client):
    client = make_client("error")
    with pytest.raises(RetrievalWorkerError) as excinfo:
        client.retrieve("x", timeout=10)
    assert "OSError" in str(excinfo.value) and "index unavailable" in str(excinfo.value)
    # worker 仍活著；回錯誤不等於崩潰
    assert client.status()["pid"] is not None


def test_shutdown_releases_process_and_status_returns_cold(make_client):
    client = make_client("ok")
    client.retrieve("q", timeout=10)
    pid = client.status()["pid"]
    assert pid
    status = client.shutdown()
    assert status["state"] == "cold" and status["pid"] is None and status["warmup"] is None


def test_background_warmup_is_idempotent(make_client):
    client = make_client("ok")
    first = client.warmup_in_background(reason="test")
    assert first["state"] in ("warming", "ready")
    client._warmup_thread.join(timeout=10)
    assert client.status()["state"] == "ready"
    again = client.warmup_in_background(reason="test")
    assert again["state"] == "ready" and client.status()["spawns"] == 1


# ---- 2. 啟動預熱的閘門 ----


class _Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        value = self.data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value


def test_startup_warmup_skips_without_index(monkeypatch):
    monkeypatch.setattr(client_module, "get_config", lambda: _Cfg({}))
    monkeypatch.setattr(client_module, "index_present", lambda: False)
    called = []
    monkeypatch.setattr(client_module.retrieval_client, "warmup_in_background", lambda reason: called.append(reason))
    assert client_module.maybe_warmup_on_start() == {"warmup": "skipped", "reason": "no_index_present"}
    assert called == []


def test_startup_warmup_respects_mode_and_switch(monkeypatch):
    monkeypatch.setattr(client_module, "index_present", lambda: True)
    called = []
    monkeypatch.setattr(client_module.retrieval_client, "warmup_in_background", lambda reason: called.append(reason))

    monkeypatch.setattr(client_module, "get_config", lambda: _Cfg({"rag": {"retrieval": {"mode": "in_process"}}}))
    assert client_module.maybe_warmup_on_start()["reason"] == "in_process_mode"
    monkeypatch.setattr(client_module, "get_config", lambda: _Cfg({"rag": {"retrieval": {"warmup_on_start": False}}}))
    assert client_module.maybe_warmup_on_start()["reason"] == "warmup_on_start_disabled"
    monkeypatch.setattr(client_module, "get_config", lambda: _Cfg({"rag": {"enabled": False}}))
    assert client_module.maybe_warmup_on_start()["reason"] == "rag_disabled"
    assert called == []

    monkeypatch.setattr(client_module, "get_config", lambda: _Cfg({}))
    assert client_module.maybe_warmup_on_start() == {"warmup": "started", "reason": "startup"}
    assert called == ["startup"]


def test_unknown_mode_falls_back_to_worker(monkeypatch):
    monkeypatch.setattr(client_module, "get_config", lambda: _Cfg({"rag": {"retrieval": {"mode": "weird"}}}))
    assert client_module.retrieval_mode() == "worker"


# ---- 3. worker 端協定 ----


def test_worker_handle_request_shapes(monkeypatch):
    from rag import retrieval_worker as worker_module

    class _Cit:
        def model_dump(self):
            return {"chunk_id": "c1", "content": "x"}

    class _Registry:
        def retrieve(self, **kwargs):
            assert kwargs["query"] == "問題" and kwargs["top_k"] == 4
            return [_Cit()]

    import rag.retrieval.registry as registry_module

    monkeypatch.setattr(registry_module, "retriever_registry", _Registry())
    response = worker_module.handle_request({"id": "r1", "op": "retrieve", "query": "問題", "top_k": 4})
    assert response["id"] == "r1" and response["ok"] is True
    assert response["result"]["citations"] == [{"chunk_id": "c1", "content": "x"}]
    assert "elapsed_ms" in response["result"]

    empty = worker_module.handle_request({"id": "r2", "op": "retrieve", "query": "   "})
    assert empty["result"]["citations"] == []

    bad = worker_module.handle_request({"id": "r3", "op": "nope"})
    assert bad["ok"] is False and bad["error"]["type"] == "ValueError"

    ping = worker_module.handle_request({"id": "r4", "op": "ping"})
    assert ping["ok"] is True and ping["result"]["protocol"] == worker_module.PROTOCOL_VERSION


def test_worker_reports_exception_instead_of_dying(monkeypatch):
    from rag import retrieval_worker as worker_module

    class _Broken:
        def retrieve(self, **kwargs):
            raise OSError("chroma locked")

    import rag.retrieval.registry as registry_module

    monkeypatch.setattr(registry_module, "retriever_registry", _Broken())
    response = worker_module.handle_request({"id": "r", "op": "retrieve", "query": "q"})
    assert response == {"id": "r", "ok": False, "error": {"type": "OSError", "message": "chroma locked"}}


def test_real_worker_process_speaks_protocol_without_loading_index(tmp_path):
    """真的啟動 `python -m rag.retrieval_worker`，只送 ping：不碰 Chroma、BM25 或 embedding。"""
    import os
    import subprocess

    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-m", "rag.retrieval_worker"],
        cwd=str(project_root),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )
    try:
        out, err = proc.communicate(
            json.dumps({"id": "p", "op": "ping"}) + "\n" + json.dumps({"id": "s", "op": "shutdown"}) + "\n",
            timeout=60,
        )
    finally:
        if proc.poll() is None:
            proc.kill()
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert lines[0]["event"] == "hello"
    assert {"id": "p", "ok": True} .items() <= lines[1].items()
    assert lines[2] == {"id": "s", "ok": True, "result": {"stopped": True}}
    assert proc.returncode == 0


# ---- 4. 靜態策略目錄與 API ----


def test_strategy_catalog_matches_registry():
    from rag.retrieval.catalog import DEFAULT_STRATEGY, STRATEGY_CATALOG
    from rag.retrieval.registry import retriever_registry

    assert DEFAULT_STRATEGY == retriever_registry.default_strategy
    assert STRATEGY_CATALOG == retriever_registry.list_strategies()


def test_router_does_not_import_registry_at_module_level():
    """主服務 import rag.router 不得順帶建立 Chroma client（那會載入索引目錄）。"""
    source = (Path(__file__).resolve().parents[1] / "rag" / "router.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        # 只看沒有縮排的模組層 import；函式內的 lazy import 是 in_process 模式刻意保留的
        if line.startswith(("from rag.retrieval.registry", "from rag.vector_store", "from rag.retriever ")):
            pytest.fail(f"rag/router.py 在模組層 import 了會載入索引的模組：{line.strip()}")
    assert "from rag.retrieval.catalog import" in source


def test_importing_server_does_not_load_index_libraries():
    """真正的收據：在乾淨的直譯器 import core.server，chromadb／fastembed／rank_bm25 都不得被載入。"""
    import subprocess

    project_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; import core.server; "
        "print(sorted(m for m in ('chromadb', 'fastembed', 'rank_bm25', 'jieba') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(project_root), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "[]"


def test_retrieval_status_and_warmup_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    import rag.router as router_module
    from core.server import app

    client = TestClient(app)
    fake_state = {"mode": "worker", "state": "cold", "pid": None}
    monkeypatch.setattr(router_module.retrieval_client, "status", lambda: dict(fake_state))
    monkeypatch.setattr(
        router_module.retrieval_client, "warmup_in_background",
        lambda reason: {**fake_state, "state": "warming", "reason": reason},
    )
    monkeypatch.setattr(router_module.retrieval_client, "shutdown", lambda: {**fake_state, "state": "cold"})

    res = client.get("/api/v1/rag/retrieval/status", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and res.json()["state"] == "cold"

    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "worker")
    res = client.post("/api/v1/rag/retrieval/warmup", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and res.json()["state"] == "warming" and res.json()["reason"] == "dashboard"

    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "in_process")
    res = client.post("/api/v1/rag/retrieval/warmup", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 409

    res = client.post("/api/v1/rag/retrieval/shutdown", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and res.json()["state"] == "cold"


# ---- 5. 對話串流在 worker 模式的降級 ----


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _Req:
    def __init__(self):
        self.messages = [_Msg("user", "測試問題")]
        self.enable_rag = True
        self.custom_system_prompt = None
        self.provider = "gemini"
        self.model = None
        self.retrieval_strategy = "hybrid_rrf"
        self.top_k = 3
        self.hybrid_alpha = 0.5
        self.score_threshold = 0.0


def _collect(req):
    from rag.router import chat_stream

    async def run():
        response = await chat_stream(req)
        return "".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(run())


def test_chat_uses_worker_citations_in_prompt(monkeypatch, make_client):
    import rag.router as router_module
    from rag import llm_gateway as gateway_module

    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "worker")
    monkeypatch.setattr(router_module, "retrieval_client", make_client("ok"))
    captured = {}

    async def ok_stream(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        yield "回答"

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", ok_stream)
    payload = _collect(_Req())
    assert payload.splitlines()[-2].startswith("event: done") or "event: done" in payload
    assert "命中 測試問題" in payload  # citations 事件帶回 worker 的結果
    assert "參考知識庫文件切片" in (captured.get("system_prompt") or "")
    assert "a.md" in captured["system_prompt"]


def test_chat_degrades_when_worker_times_out(monkeypatch, make_client):
    import rag.router as router_module
    from rag import llm_gateway as gateway_module

    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "worker")
    monkeypatch.setattr(router_module, "RETRIEVAL_TIMEOUT_SECONDS", 1)
    client = make_client("hang")
    monkeypatch.setattr(router_module, "retrieval_client", client)
    captured = {}

    async def ok_stream(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        yield "仍然回答"

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", ok_stream)
    payload = _collect(_Req())
    assert "event: done" in payload and "仍然回答" in payload
    assert "參考知識庫文件切片" not in (captured.get("system_prompt") or "")
    assert client.status()["state"] == "failed"  # 卡住的 worker 已被終止，不會留在主服務裡


def test_chat_survives_worker_crash(monkeypatch, make_client):
    import rag.router as router_module
    from rag import llm_gateway as gateway_module

    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "worker")
    monkeypatch.setattr(router_module, "retrieval_client", make_client("crash"))

    async def ok_stream(**kwargs):
        yield "照常回答"

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", ok_stream)
    payload = _collect(_Req())
    assert "event: done" in payload and "照常回答" in payload
