"""Destructive RAG index lifecycle operations, used only by confirmed worker jobs."""

from __future__ import annotations

from typing import Callable, Optional

from core.database import get_db
from core.models import RAGIndexedFile, RAGIndexedFolder
from rag.retriever import bm25_service
from rag.storage import compact_sqlite, verify_index_consistency
from rag.vector_store import vector_store


def remove_folder_index(folder_id: int, report: Callable[..., None]) -> dict:
    db = get_db()
    with db.session_scope() as session:
        folder = session.query(RAGIndexedFolder).filter_by(id=folder_id).first()
        if not folder:
            raise ValueError("找不到指定的資料夾")
        paths = [row.path for row in session.query(RAGIndexedFile.path).filter_by(folder_id=folder_id).all()]
        folder_path = folder.path

    report(message=f"正在移除 {len(paths)} 個檔案的 RAG 索引")
    vector_store.delete_by_file_paths(paths)
    bm25_service.delete_by_file_paths(paths)

    with db.session_scope() as session:
        session.query(RAGIndexedFile).filter_by(folder_id=folder_id).delete()
        session.query(RAGIndexedFolder).filter_by(id=folder_id).delete()

    sqlite = compact_sqlite()
    consistency = verify_index_consistency()
    return {
        "operation": "remove_folder",
        "folder_path": folder_path,
        "removed_files": len(paths),
        "sqlite": sqlite,
        "consistency": consistency,
        "chroma_reclamation": "logical_delete",
    }


def clear_all_rag_indexes(report: Callable[..., None]) -> dict:
    """Clear only RAG folder/file/vector/BM25 data; chat history and source files remain."""
    db = get_db()
    with db.session_scope() as session:
        file_count = session.query(RAGIndexedFile).count()
        folder_count = session.query(RAGIndexedFolder).count()

    report(message="正在清空 Chroma、BM25 與 RAG 資料夾索引紀錄")
    vector_store.clear()
    bm25_service.clear()
    with db.session_scope() as session:
        session.query(RAGIndexedFile).delete()
        session.query(RAGIndexedFolder).delete()

    sqlite = compact_sqlite()
    consistency = verify_index_consistency()
    return {
        "operation": "clear_all",
        "removed_files": file_count,
        "removed_folders": folder_count,
        "sqlite": sqlite,
        "consistency": consistency,
        "chroma_reclamation": "collection_reset",
    }


def rebuild_bm25_from_chroma(
    report: Callable[..., None],
    batch_size: int = 2000,
    control_checkpoint: Optional[Callable[[], None]] = None,
) -> dict:
    """Recreate the sparse index from existing Chroma documents without rereading source folders."""
    total = vector_store.count()
    chunks = []
    report(total_files=total, processed_files=0, message="正從 Chroma 重建 BM25；不會重新掃描來源檔案")
    for offset in range(0, total, batch_size):
        if control_checkpoint:
            control_checkpoint()
        batch = vector_store.collection.get(
            include=["documents", "metadatas"],
            limit=batch_size,
            offset=offset,
        )
        ids = batch.get("ids") or []
        documents = batch.get("documents") or []
        metadatas = batch.get("metadatas") or []
        for chunk_id, document, metadata in zip(ids, documents, metadatas):
            chunks.append({
                "chunk_id": chunk_id,
                "content": document or "",
                "metadata": metadata or {},
            })
        report(
            processed_files=min(offset + len(ids), total),
            current_file=f"BM25 batch {offset // batch_size + 1}",
            status="indexing",
        )

    if control_checkpoint:
        control_checkpoint()
    bm25_service.build_index(chunks)
    consistency = verify_index_consistency()
    return {
        "operation": "rebuild_bm25",
        "rebuilt_chunks": len(chunks),
        "consistency": consistency,
    }
