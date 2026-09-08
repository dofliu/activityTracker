"""RAG index storage accounting, consistency audit and bounded reclamation."""

from __future__ import annotations

import os
import json
import re
import shutil
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


# ---- Chroma 目錄的空間帳（ADR-009 Addendum C）-----------------------------
#
# Chroma 的 `delete_collection` 只做邏輯刪除：實測（2026-09-07，chromadb 1.5.9）
# 刪掉一個 4,000 切片的 collection 之後，目錄大小 14,209,408 bytes 一個位元組都沒少——
# chroma.sqlite3 的 1,791 頁裡有 1,590 頁變成空頁（VACUUM 後 7.3 MB → 0.8 MB），
# 而舊的 HNSW 片段目錄整個留在原地，且不再被 segments 表引用。
# 所以「索引只有幾千個切片，chroma 目錄卻是好幾 GB」是預期中的殘留，不是壞掉。

_SEGMENT_DIR_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_CHROMA_SQLITE_NAME = "chroma.sqlite3"


def _chroma_sqlite_paths() -> list[Path]:
    root = rag_settings.CHROMA_DIR
    return [root / f"{_CHROMA_SQLITE_NAME}{suffix}" for suffix in ("", "-wal", "-shm")]


def _live_segment_ids() -> tuple[set[str], str | None]:
    """讀 chroma.sqlite3 的 segments 表；讀不到就回原因，呼叫端一律當作「不知道」。"""
    db = rag_settings.CHROMA_DIR / _CHROMA_SQLITE_NAME
    if not db.is_file():
        return set(), "no_chroma_sqlite"
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as connection:
            rows = connection.execute("SELECT id FROM segments").fetchall()
        return {str(row[0]) for row in rows}, None
    except Exception as exc:  # noqa: BLE001 — 讀不到就誠實說讀不到，不猜
        return set(), f"{type(exc).__name__}: {exc}"


def chroma_report() -> dict[str, Any]:
    """Chroma 目錄裡哪些空間是活的、哪些是刪掉之後沒有回收的。

    只讀檔案大小與 chroma.sqlite3（唯讀連線），**不 import chromadb**——主服務
    不載入索引套件的邊界（ADR-009 §6）在這裡一樣成立。
    """
    root = rag_settings.CHROMA_DIR
    live, unreadable = _live_segment_ids()
    sqlite_bytes = sum(_disk_bytes(path) for path in _chroma_sqlite_paths())

    free_bytes = 0
    if unreadable is None:
        try:
            db = root / _CHROMA_SQLITE_NAME
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as connection:
                page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
                free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            free_bytes = page_size * free_pages
        except Exception:  # noqa: BLE001 — 空頁數拿不到就當 0，不影響孤兒判定
            free_bytes = 0

    live_dirs: list[dict[str, Any]] = []
    orphan_dirs: list[dict[str, Any]] = []
    unknown_entries: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.is_file() and entry.name.startswith(_CHROMA_SQLITE_NAME):
            continue
        item = {"name": entry.name, "bytes": _disk_bytes(entry)}
        if not (entry.is_dir() and _SEGMENT_DIR_RE.match(entry.name)):
            unknown_entries.append(item)          # 認不得就只回報，永遠不刪
        elif unreadable is not None:
            unknown_entries.append(item)          # 讀不到 segments 表就一律不算孤兒
        elif entry.name in live:
            live_dirs.append(item)
        else:
            orphan_dirs.append(item)

    orphan_bytes = sum(item["bytes"] for item in orphan_dirs)
    return {
        "path": str(root),
        "total_bytes": _disk_bytes(root),
        "sqlite_bytes": sqlite_bytes,
        "sqlite_free_bytes": free_bytes,
        "live_segment_dirs": live_dirs,
        "orphan_dirs": orphan_dirs,
        "unknown_entries": unknown_entries,
        "orphan_bytes": orphan_bytes,
        "reclaimable_bytes": orphan_bytes + free_bytes,
        "segments_readable": unreadable is None,
        "reason": unreadable,
        "claim_boundary": (
            "只比對目錄名稱與 chroma.sqlite3 的 segments 表，並讀 SQLite 空頁數；"
            "讀不到 segments 表就一律不算孤兒（也不會刪）。不代表索引內容正確。"
        ),
    }


def compact_chroma(confirm: bool = False) -> dict[str, Any]:
    """回收 Chroma 目錄：刪掉沒有被引用的片段目錄，並 VACUUM chroma.sqlite3。

    三條 fail-closed 規則：讀不到 segments 表就什麼都不刪；只刪名稱是 UUID 且
    不在 segments 表裡的目錄；刪不掉（檔案被開著）如實記在 failed_dirs，不重試也不隱藏。
    """
    if not confirm:
        raise ValueError("compact_chroma 需要 confirm=True")
    before = chroma_report()
    if not before["segments_readable"]:
        raise RuntimeError(
            f"讀不到 chroma.sqlite3 的 segments 表（{before['reason']}），"
            "無法判斷哪些片段是孤兒；沒有刪除任何東西。"
        )

    live, unreadable = _live_segment_ids()   # 刪之前再確認一次，不用舊快照
    if unreadable is not None:
        raise RuntimeError(f"刪除前重讀 segments 表失敗（{unreadable}）；沒有刪除任何東西。")

    removed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in before["orphan_dirs"]:
        target = rag_settings.CHROMA_DIR / item["name"]
        if item["name"] in live or not _SEGMENT_DIR_RE.match(item["name"]) or not target.is_dir():
            continue
        try:
            shutil.rmtree(target)
            removed.append(item)
        except OSError as exc:
            failed.append({**item, "error": f"{type(exc).__name__}: {exc}"})

    vacuum: dict[str, Any] = {"ran": False}
    db = rag_settings.CHROMA_DIR / _CHROMA_SQLITE_NAME
    if db.is_file():
        try:
            with closing(sqlite3.connect(db, timeout=30)) as connection:
                connection.execute("PRAGMA busy_timeout=30000")
                connection.execute("VACUUM")
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            vacuum = {"ran": True, "integrity": integrity}
        except Exception as exc:  # noqa: BLE001 — VACUUM 失敗不影響已刪除的孤兒
            vacuum = {"ran": False, "error": f"{type(exc).__name__}: {exc}"}

    after = chroma_report()
    return {
        "operation": "compact_chroma",
        "before_bytes": before["total_bytes"],
        "after_bytes": after["total_bytes"],
        "reclaimed_bytes": max(0, before["total_bytes"] - after["total_bytes"]),
        "removed_dirs": removed,
        "failed_dirs": failed,
        "vacuum": vacuum,
        "before": before,
        "after": after,
    }
