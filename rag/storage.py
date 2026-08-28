"""RAG index storage accounting, consistency audit and bounded reclamation."""

from __future__ import annotations

import os
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import func

from core.data_lifecycle import configured_database_path, verify_sqlite_database
from core.database import get_db
from core.models import RAGIndexedFile, RAGIndexJob
from rag.config import rag_settings


def _disk_bytes(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def storage_report() -> dict[str, Any]:
    db = get_db()
    with db.session_scope() as session:
        source_files, source_bytes, source_chunks = session.query(
            func.count(RAGIndexedFile.id),
            func.coalesce(func.sum(RAGIndexedFile.file_size), 0),
            func.coalesce(func.sum(RAGIndexedFile.chunk_count), 0),
        ).one()

        latest_receipt = (
            session.query(RAGIndexJob.result_json)
            .filter(RAGIndexJob.result_json.isnot(None))
            .order_by(RAGIndexJob.completed_at.desc())
            .first()
        )
    try:
        receipt = json.loads(latest_receipt[0]) if latest_receipt and latest_receipt[0] else {}
    except (TypeError, ValueError):
        receipt = {}
    snapshot = receipt.get("storage") or receipt.get("consistency") or {}
    vector_chunks = snapshot.get("vector_chunks")
    bm25_chunks = snapshot.get("bm25_chunks")
    chroma_path = rag_settings.CHROMA_DIR
    bm25_path = rag_settings.BM25_PATH
    sqlite_path = configured_database_path()
    sqlite_aux_bytes = sum(
        _disk_bytes(Path(f"{sqlite_path}{suffix}")) for suffix in ("-wal", "-shm")
    )
    mismatch = (int(vector_chunks) - int(source_chunks or 0)) if vector_chunks is not None else None
    return {
        "source_files": int(source_files or 0),
        "source_bytes": int(source_bytes or 0),
        "source_chunks": int(source_chunks or 0),
        "vector_chunks": int(vector_chunks) if vector_chunks is not None else None,
        "bm25_chunks": int(bm25_chunks) if bm25_chunks is not None else None,
        "vector_delta": int(mismatch) if mismatch is not None else None,
        "consistency": (
            "unverified" if mismatch is None
            else "matched" if mismatch == 0 and (bm25_chunks is None or bm25_chunks == int(source_chunks or 0))
            else "mismatch"
        ),
        "bm25_loaded": bm25_chunks is not None,
        "chroma_bytes": snapshot.get("chroma_bytes"),
        "bm25_bytes": snapshot.get("bm25_bytes"),
        "sqlite_bytes": _disk_bytes(sqlite_path) + sqlite_aux_bytes,
        "sqlite_aux_bytes": sqlite_aux_bytes,
        "index_bytes": snapshot.get("index_bytes"),
        "paths": {
            "chroma": str(chroma_path),
            "bm25": str(bm25_path),
            "sqlite": str(sqlite_path),
        },
    }


def compact_sqlite() -> dict[str, Any]:
    """Checkpoint and VACUUM only after a deletion worker completed its writes."""
    path = configured_database_path()
    before = _disk_bytes(path)
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        connection.execute("PRAGMA busy_timeout=10000")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "sqlite_before_bytes": before,
        "sqlite_after_bytes": _disk_bytes(path),
        "checkpoint": {
            "busy": bool(checkpoint[0]),
            "log_pages": checkpoint[1],
            "checkpointed_pages": checkpoint[2],
        },
        "integrity": integrity,
    }


def verify_index_consistency() -> dict[str, Any]:
    report = storage_report()
    # 只在 worker 呼叫此函式：避免 dashboard API 直接讀取大型 Chroma/BM25。
    from rag.retriever import bm25_service
    from rag.vector_store import vector_store
    vector_chunks = vector_store.count()
    bm25_service._ensure_loaded()
    bm25_chunks = len(bm25_service.corpus_chunks)
    source_chunks = int(report["source_chunks"] or 0)
    report.update({
        "vector_chunks": vector_chunks,
        "bm25_chunks": bm25_chunks,
        "vector_delta": vector_chunks - source_chunks,
        "consistency": "matched" if vector_chunks == source_chunks and bm25_chunks == source_chunks else "mismatch",
        "bm25_loaded": True,
        "chroma_bytes": _disk_bytes(rag_settings.CHROMA_DIR),
        "bm25_bytes": _disk_bytes(rag_settings.BM25_PATH),
        "index_bytes": _disk_bytes(rag_settings.CHROMA_DIR) + _disk_bytes(rag_settings.BM25_PATH),
    })
    report["sqlite_integrity"] = verify_sqlite_database(configured_database_path())["integrity"]
    return report
