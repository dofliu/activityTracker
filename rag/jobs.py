"""DeskRAG worker job control.

The web process only creates and observes jobs.  File traversal, parsing and
embedding are deliberately executed by ``rag.index_worker`` in another OS
process so a long index cannot block the dashboard or collectors.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.database import get_db
from core.models import RAGIndexJob
from core.time_utils import get_local_now
from rag.config import rag_settings


ACTIVE_STATUSES = {"queued", "running", "scanning", "indexing", "paused", "cancelling"}
TERMINAL_STATUSES = {"completed", "completed_limited", "cancelled", "failed"}


def serialize_job(job: RAGIndexJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    now = get_local_now()
    started = job.started_at or job.requested_at
    elapsed = round((now - started).total_seconds(), 1) if started else 0
    total = int(job.total_files or 0)
    processed = int(job.processed_files or 0)
    return {
        "id": job.id,
        "job_type": job.job_type,
        "folder_id": job.folder_id,
        "status": job.status,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "worker_pid": job.worker_pid,
        "total_files": total,
        "processed_files": processed,
        "progress_percent": round(processed / total * 100, 1) if total else 0,
        "indexed_chunks": int(job.indexed_chunks or 0),
        "error_count": int(job.error_count or 0),
        "max_files": job.max_files,
        "throttle_ms": int(job.throttle_ms or 0),
        "current_file": job.current_file or "",
        "message": job.message or "",
        "error_message": job.error_message,
        "pause_requested": bool(job.pause_requested),
        "cancel_requested": bool(job.cancel_requested),
        "result": _decode_result(job.result_json),
        # 暫停仍是尚未完成的工作，前端必須保持輪詢並提供「恢復」。
        "is_running": job.status in ACTIVE_STATUSES,
        "elapsed_seconds": elapsed,
    }


def _decode_result(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def get_latest_job(include_terminal: bool = True) -> dict[str, Any] | None:
    db = get_db()
    with db.session_scope() as session:
        query = session.query(RAGIndexJob)
        if not include_terminal:
            query = query.filter(RAGIndexJob.status.in_(ACTIVE_STATUSES))
        return serialize_job(query.order_by(RAGIndexJob.requested_at.desc()).first())


def get_job(job_id: str) -> dict[str, Any] | None:
    db = get_db()
    with db.session_scope() as session:
        return serialize_job(session.query(RAGIndexJob).filter_by(id=job_id).first())


def create_job(
    job_type: str,
    folder_id: int | None = None,
    max_files: int | None = None,
    throttle_ms: int | None = None,
) -> dict[str, Any]:
    if job_type not in {"index", "remove_folder", "clear_all", "audit", "rebuild_bm25", "activity_sync"}:
        raise ValueError(f"Unsupported RAG job type: {job_type}")

    db = get_db()
    with db.session_scope() as session:
        active = (
            session.query(RAGIndexJob)
            .filter(RAGIndexJob.status.in_(ACTIVE_STATUSES))
            .order_by(RAGIndexJob.requested_at.desc())
            .first()
        )
        if active:
            raise RuntimeError(f"已有進行中的 RAG 工作 ({active.id})")

        job = RAGIndexJob(
            id=str(uuid.uuid4()),
            job_type=job_type,
            folder_id=folder_id,
            status="queued",
            requested_at=get_local_now(),
            updated_at=get_local_now(),
            max_files=(
                max(1, int(max_files)) if max_files is not None else rag_settings.INDEX_MAX_FILES_PER_RUN
            ) if job_type == "index" else None,
            throttle_ms=(
                max(0, int(throttle_ms)) if throttle_ms is not None else rag_settings.INDEX_THROTTLE_MS
            ) if job_type == "index" else 0,
            message="等待獨立索引 worker 啟動",
        )
        session.add(job)
        session.flush()
        return serialize_job(job) or {}


def launch_worker(job_id: str) -> dict[str, Any]:
    """Spawn a hidden, detached-enough child while retaining its PID for diagnostics."""
    project_root = Path(__file__).resolve().parent.parent
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "-m", "rag.index_worker", "--job-id", job_id],
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        env=os.environ.copy(),
    )
    update_job(job_id, worker_pid=process.pid, message="獨立索引 worker 已啟動")
    return get_job(job_id) or {"id": job_id, "worker_pid": process.pid}


def update_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    db = get_db()
    with db.session_scope() as session:
        job = session.query(RAGIndexJob).filter_by(id=job_id).first()
        if not job:
            return
        for name, value in values.items():
            if hasattr(job, name):
                setattr(job, name, value)
        job.updated_at = get_local_now()


def start_job(job_id: str) -> dict[str, Any] | None:
    update_job(
        job_id,
        status="running",
        started_at=get_local_now(),
        message="worker 正在準備索引工作",
        error_message=None,
    )
    return get_job(job_id)


def finish_job(
    job_id: str,
    status: str,
    message: str = "",
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"Invalid terminal RAG job status: {status}")
    update_job(
        job_id,
        status=status,
        completed_at=get_local_now(),
        message=message,
        error_message=error_message,
        pause_requested=0,
        result_json=json.dumps(result, ensure_ascii=False) if result is not None else None,
    )


def request_pause(job_id: str, paused: bool) -> dict[str, Any] | None:
    db = get_db()
    with db.session_scope() as session:
        job = session.query(RAGIndexJob).filter_by(id=job_id).first()
        if not job:
            return None
        if job.status in TERMINAL_STATUSES:
            return serialize_job(job)
        job.pause_requested = 1 if paused else 0
        if paused:
            job.message = "暫停請求已送出；目前檔案處理完成後會暫停"
        elif job.status == "paused":
            job.status = "running"
            job.message = "已恢復索引工作"
        job.updated_at = get_local_now()
        return serialize_job(job)


def request_cancel(job_id: str) -> dict[str, Any] | None:
    db = get_db()
    with db.session_scope() as session:
        job = session.query(RAGIndexJob).filter_by(id=job_id).first()
        if not job:
            return None
        if job.status not in TERMINAL_STATUSES:
            job.cancel_requested = 1
            job.pause_requested = 0
            job.status = "cancelling"
            job.message = "取消請求已送出；不會中斷正在寫入的單一批次"
            job.updated_at = get_local_now()
        return serialize_job(job)


def control_state(job_id: str) -> tuple[bool, bool]:
    """Return (pause, cancel); a new short-lived session avoids stale worker state."""
    db = get_db()
    with db.session_scope() as session:
        job = session.query(RAGIndexJob).filter_by(id=job_id).first()
        if not job:
            return False, True
        return bool(job.pause_requested), bool(job.cancel_requested)
