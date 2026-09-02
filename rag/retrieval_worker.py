"""Resident DeskRAG retrieval worker.

主服務不再在自己的程序內載入 Chroma、BM25 pickle 與 embedding 模型；
它把每一次檢索交給這個常駐子程序（`python -m rag.retrieval_worker`），
以 stdin/stdout 的 JSON lines 對話：

    → {"id": "…", "op": "warmup"}
    ← {"id": "…", "ok": true, "result": {"bm25_chunks": …, "vector_chunks": …}}
    → {"id": "…", "op": "retrieve", "query": "…", "strategy": "hybrid_rrf", "top_k": 6}
    ← {"id": "…", "ok": true, "result": {"citations": [...], "elapsed_ms": …}}

契約：
- stdout **只**承載協定訊息；程序一啟動就把 fd 1 改接到 stderr，任何第三方
  套件的 print／進度條都不會污染協定通道。
- stdin 關閉（主服務結束或 kill）即自行退出，不會留下孤兒程序。
- 回傳內容只包含 citation（本就會送到瀏覽器的資料）與計數／耗時；
  不包含 prompt、金鑰或任何設定值。
- 這個程序不做寫入：索引、刪除與重建仍由 `rag.index_worker` 的 job 負責。
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 1

logger = logging.getLogger("OmniContext.RAG.RetrievalWorker")


def _rss_mb() -> Optional[float]:
    try:
        import psutil

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001 — 記憶體數值只是診斷用，取不到就省略
        return None


def warmup() -> Dict[str, Any]:
    """Load BM25、Chroma collection 與 embedding 模型，回傳不含內容的收據。"""
    from rag.config import rag_settings
    from rag.embeddings import embedding_service
    from rag.retriever import bm25_service
    from rag.vector_store import vector_store

    durations: Dict[str, int] = {}
    started = time.perf_counter()

    step = time.perf_counter()
    bm25_service._ensure_loaded()
    durations["bm25_ms"] = int((time.perf_counter() - step) * 1000)

    step = time.perf_counter()
    vector_chunks = vector_store.count()
    durations["chroma_ms"] = int((time.perf_counter() - step) * 1000)

    step = time.perf_counter()
    embedding_ready = True
    embedding_error: Optional[str] = None
    try:
        embedding_service.embed_query("warmup")
    except Exception as exc:  # noqa: BLE001 — 模型載入失敗要如實回報而不是讓 worker 死掉
        embedding_ready = False
        embedding_error = f"{type(exc).__name__}: {exc}"[:200]
    durations["embedding_ms"] = int((time.perf_counter() - step) * 1000)
    durations["total_ms"] = int((time.perf_counter() - started) * 1000)

    return {
        "bm25_chunks": len(bm25_service.corpus_chunks),
        "vector_chunks": int(vector_chunks),
        "embedding_provider": rag_settings.DEFAULT_EMBEDDING_PROVIDER,
        "embedding_ready": embedding_ready,
        "embedding_error": embedding_error,
        "durations": durations,
        "worker_rss_mb": _rss_mb(),
    }


def retrieve(request: Dict[str, Any]) -> Dict[str, Any]:
    from rag.retrieval.registry import retriever_registry

    query = str(request.get("query") or "")
    if not query.strip():
        return {"citations": [], "elapsed_ms": 0}
    started = time.perf_counter()
    citations = retriever_registry.retrieve(
        query=query,
        strategy=request.get("strategy"),
        top_k=request.get("top_k"),
        alpha=request.get("alpha"),
        score_threshold=float(request.get("score_threshold") or 0.0),
    )
    return {
        "citations": [c.model_dump() for c in citations],
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "worker_rss_mb": _rss_mb(),
    }


def handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Pure dispatcher so tests can exercise the protocol without a subprocess."""
    request_id = request.get("id")
    op = request.get("op")
    try:
        if op == "ping":
            result: Dict[str, Any] = {"pid": os.getpid(), "protocol": PROTOCOL_VERSION}
        elif op == "warmup":
            result = warmup()
        elif op == "retrieve":
            result = retrieve(request)
        else:
            raise ValueError(f"Unsupported retrieval op: {op!r}")
        return {"id": request_id, "ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001 — 任何錯誤都要回給主服務，不能默默吞掉
        return {
            "id": request_id,
            "ok": False,
            "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }


def _protocol_channel() -> io.TextIOWrapper:
    """Reserve the real stdout for protocol lines and send everything else to stderr."""
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return io.TextIOWrapper(
        os.fdopen(protocol_fd, "wb", buffering=0),
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
        newline="\n",
    )


def main() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    out = _protocol_channel()
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — 舊版或非 tty stdin 不支援 reconfigure 也無妨
        pass

    def emit(payload: Dict[str, Any]) -> None:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()

    emit({"event": "hello", "pid": os.getpid(), "protocol": PROTOCOL_VERSION})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            emit({"id": None, "ok": False, "error": {"type": "ValueError", "message": "invalid JSON request"}})
            continue
        if request.get("op") == "shutdown":
            emit({"id": request.get("id"), "ok": True, "result": {"stopped": True}})
            break
        try:
            emit(handle_request(request))
        except Exception:  # noqa: BLE001 — 序列化失敗等極端情況
            traceback.print_exc(file=sys.stderr)
            emit({"id": request.get("id"), "ok": False, "error": {"type": "InternalError", "message": "worker failed to serialize response"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
