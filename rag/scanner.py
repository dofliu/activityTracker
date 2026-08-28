import os
import hashlib
import asyncio
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from rag.config import rag_settings
from rag.parsers.parser_hub import parser_hub
from rag.chunker import chunker
from rag.vector_store import vector_store
from rag.retriever import bm25_service
from core.database import get_db
from core.models import RAGIndexedFolder, RAGIndexedFile
from core.time_utils import get_local_now
from rag.jobs import control_state, update_job

logger = logging.getLogger("OmniContext.RAG.Scanner")


class IndexingProgress:
    def __init__(self):
        self.is_running = False
        self.status = "idle"  # idle, scanning, indexing, completed, error
        self.total_files = 0
        self.processed_files = 0
        self.indexed_chunks = 0
        self.current_file = ""
        self.start_time = 0
        self.elapsed_seconds = 0
        self.error_count = 0
        self.logs: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "status": self.status,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "progress_percent": round((self.processed_files / self.total_files * 100), 1) if self.total_files > 0 else 0,
            "indexed_chunks": self.indexed_chunks,
            "current_file": self.current_file,
            "elapsed_seconds": round(time.time() - self.start_time, 1) if self.is_running else self.elapsed_seconds,
            "error_count": self.error_count,
            "recent_logs": self.logs[-20:]
        }

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]
        logger.info(message)


progress = IndexingProgress()


class FileScanner:
    def __init__(self):
        self._lock = asyncio.Lock()

    def _should_ignore_dir(self, dir_name: str) -> bool:
        return dir_name.lower() in rag_settings.IGNORE_DIRS or dir_name.startswith("$")

    def _should_index_file(self, filename: str) -> bool:
        # Ignore temporary prefix files (e.g. ~$*.docx, .~*) and hidden files
        for prefix in getattr(rag_settings, "IGNORE_PREFIXES", {"~$", ".~", ".#", ".~lock."}):
            if filename.startswith(prefix):
                return False
        if filename.startswith("."):
            return False

        ext = Path(filename).suffix.lower()
        if ext in rag_settings.IGNORE_EXTS:
            return False
        if ext in rag_settings.DOCUMENT_EXTS or ext in rag_settings.CODE_EXTS or ext in rag_settings.IMAGE_EXTS:
            return True
        return False

    def _compute_file_hash(self, file_path: str) -> str:
        # Hash first 64KB + file size + mod time for fast hashing on large files
        try:
            stat = os.stat(file_path)
            hasher = hashlib.md5()
            hasher.update(str(stat.st_size).encode())
            hasher.update(str(stat.st_mtime).encode())
            with open(file_path, "rb") as f:
                hasher.update(f.read(65536))
            return hasher.hexdigest()
        except (PermissionError, OSError, FileNotFoundError):
            return ""

    def scan_folder_files(self, folder_path: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
        found_files = []
        folder_p = Path(folder_path).resolve()
        if not folder_p.exists():
            return found_files

        for root, dirs, files in os.walk(folder_p):
            if job_id:
                self._check_control(job_id, "scanning")
            # In-place filter out ignored dirs to prevent descending into them
            dirs[:] = [d for d in dirs if not self._should_ignore_dir(d)]

            for file in files:
                if self._should_index_file(file):
                    full_path = str(Path(root) / file)
                    try:
                        stat = os.stat(full_path)
                        found_files.append({
                            "path": full_path,
                            "filename": file,
                            "extension": Path(file).suffix.lower(),
                            "size": stat.st_size,
                            "mtime": stat.st_mtime
                        })
                    except (PermissionError, FileNotFoundError):
                        continue
        return found_files

    def _check_control(self, job_id: Optional[str], active_status: str) -> None:
        """Cooperative pause/cancel checkpoint; never terminates an active write batch."""
        if not job_id:
            return
        paused, cancelled = control_state(job_id)
        if cancelled:
            raise InterruptedError("索引工作已取消")
        while paused:
            update_job(job_id, status="paused", message="已暫停；可隨時恢復或取消")
            time.sleep(0.25)
            paused, cancelled = control_state(job_id)
            if cancelled:
                raise InterruptedError("索引工作已取消")
        update_job(job_id, status=active_status)

    def _set_job_progress(self, job_id: Optional[str], **values: Any) -> None:
        if job_id:
            update_job(job_id, **values)

    def run_indexing_job(self, job_id: Optional[str], target_folder_id: Optional[int] = None) -> str:
        """Run in the dedicated worker process; source files are read-only throughout."""
        progress.is_running = True
        progress.status = "scanning"
        progress.start_time = time.time()
        progress.processed_files = progress.indexed_chunks = progress.error_count = 0
        progress.logs.clear()
        progress.log("開始掃描知識庫目錄檔案")
        database = get_db()
        max_files = rag_settings.INDEX_MAX_FILES_PER_RUN
        throttle_ms = rag_settings.INDEX_THROTTLE_MS

        if job_id:
            from rag.jobs import get_job
            job = get_job(job_id) or {}
            max_files = int(job.get("max_files") or max_files)
            throttle_ms = int(job.get("throttle_ms") or throttle_ms)

        try:
            folders_to_scan = []
            with database.session_scope() as session:
                query = session.query(RAGIndexedFolder).filter(RAGIndexedFolder.is_active == 1)
                if target_folder_id:
                    query = query.filter(RAGIndexedFolder.id == target_folder_id)
                folders_to_scan = [{"id": f.id, "path": f.path} for f in query.all()]

            if not folders_to_scan:
                self._set_job_progress(job_id, total_files=0, processed_files=0, message="未設定或無啟用的知識庫目錄")
                return "completed"

            all_scanned_files: List[Dict[str, Any]] = []
            for folder in folders_to_scan:
                self._check_control(job_id, "scanning")
                disk_files = self.scan_folder_files(folder["path"], job_id)
                for file_info in disk_files:
                    file_info["folder_id"] = folder["id"]
                all_scanned_files.extend(disk_files)
                with database.session_scope() as session:
                    row = session.query(RAGIndexedFolder).filter_by(id=folder["id"]).first()
                    if row:
                        row.file_count = len(disk_files)
                        row.total_size = sum(item["size"] for item in disk_files)
                        row.last_scanned_at = get_local_now()

            total_discovered = len(all_scanned_files)
            work_items = all_scanned_files[:max_files]
            progress.total_files = len(work_items)
            self._set_job_progress(
                job_id,
                status="indexing",
                total_files=len(work_items),
                processed_files=0,
                indexed_chunks=0,
                error_count=0,
                message=(
                    f"發現 {total_discovered} 個可索引檔案；本次上限 {max_files} 個"
                    if total_discovered > max_files else f"發現 {total_discovered} 個可索引檔案"
                ),
            )

            # Only reconcile deletion inside the folders selected for this job.
            current_paths = {item["path"] for item in all_scanned_files}
            folder_ids = [item["id"] for item in folders_to_scan]
            with database.session_scope() as session:
                rows = session.query(RAGIndexedFile).filter(RAGIndexedFile.folder_id.in_(folder_ids)).all()
                deleted_paths = [row.path for row in rows if row.path not in current_paths]
            if deleted_paths:
                vector_store.delete_by_file_paths(deleted_paths)
                bm25_service.remove_paths_without_rebuild(deleted_paths)
                with database.session_scope() as session:
                    session.query(RAGIndexedFile).filter(RAGIndexedFile.path.in_(deleted_paths)).delete(synchronize_session=False)

            pending_bm25_chunks: List[Dict[str, Any]] = []
            changed_paths: List[str] = list(deleted_paths)
            max_size = rag_settings.INDEX_MAX_FILE_SIZE_MB * 1024 * 1024
            limited_by_size = 0

            for idx, file_info in enumerate(work_items, start=1):
                self._check_control(job_id, "indexing")
                f_path, f_name = file_info["path"], file_info["filename"]
                f_hash = self._compute_file_hash(f_path)
                progress.current_file = f_name
                progress.processed_files = idx
                self._set_job_progress(job_id, processed_files=idx, current_file=f_name)

                if file_info["size"] > max_size:
                    limited_by_size += 1
                    self._upsert_file_status(file_info, f_hash, "skipped_too_large", 0, f"超過單檔 {rag_settings.INDEX_MAX_FILE_SIZE_MB} MB 上限")
                    continue

                with database.session_scope() as session:
                    existing = session.query(RAGIndexedFile).filter_by(path=f_path).first()
                    unchanged = bool(existing and existing.file_hash == f_hash and existing.status == "indexed")
                if unchanged:
                    if throttle_ms:
                        time.sleep(throttle_ms / 1000)
                    continue

                try:
                    parsed_doc = parser_hub.parse_file(f_path)
                    chunks = chunker.chunk_document(parsed_doc)
                    vector_store.delete_by_file_path(f_path)
                    changed_paths.append(f_path)
                    if chunks:
                        vector_store.add_chunks(chunks)
                        pending_bm25_chunks.extend([
                            {
                                "chunk_id": chunk.chunk_id,
                                "content": chunk.content,
                                "metadata": {
                                    "file_path": chunk.file_path, "filename": chunk.filename,
                                    "file_type": chunk.file_type, "page": chunk.page_number,
                                    "slide": chunk.slide_number, "sheet": chunk.sheet_name,
                                    "title": chunk.section_title,
                                },
                            }
                            for chunk in chunks
                        ])
                        progress.indexed_chunks += len(chunks)
                    self._upsert_file_status(file_info, f_hash, "indexed", len(chunks), None)
                except Exception as exc:
                    progress.error_count += 1
                    changed_paths.append(f_path)
                    self._upsert_file_status(file_info, f_hash, "failed", 0, str(exc))

                self._set_job_progress(
                    job_id, indexed_chunks=progress.indexed_chunks, error_count=progress.error_count
                )
                if throttle_ms:
                    time.sleep(throttle_ms / 1000)

            # One BM25 rebuild is intentional; previous code rebuilt once per changed file.
            bm25_service.remove_paths_without_rebuild(changed_paths)
            if pending_bm25_chunks:
                bm25_service.add_or_update_chunks(pending_bm25_chunks)
            elif changed_paths:
                bm25_service.build_index(bm25_service.corpus_chunks)

            is_limited = total_discovered > len(work_items)
            message = f"完成 {len(work_items)} 個檔案，新增/更新 {progress.indexed_chunks} 個切片"
            if is_limited:
                message += f"；尚有 {total_discovered - len(work_items)} 個檔案待下次索引"
            if limited_by_size:
                message += f"；略過 {limited_by_size} 個超過大小上限的檔案"
            self._set_job_progress(job_id, message=message, current_file="")
            return "completed_limited" if is_limited else "completed"
        finally:
            progress.is_running = False
            progress.elapsed_seconds = round(time.time() - progress.start_time, 1)

    def _upsert_file_status(self, file_info: Dict[str, Any], file_hash: str, status: str, chunk_count: int, error: Optional[str]) -> None:
        database = get_db()
        with database.session_scope() as session:
            row = session.query(RAGIndexedFile).filter_by(path=file_info["path"]).first()
            if row is None:
                row = RAGIndexedFile(path=file_info["path"], filename=file_info["filename"], extension=file_info["extension"])
                session.add(row)
            row.folder_id = file_info["folder_id"]
            row.filename = file_info["filename"]
            row.extension = file_info["extension"]
            row.file_size = file_info["size"]
            row.last_modified = file_info["mtime"]
            row.file_hash = file_hash
            row.chunk_count = chunk_count
            row.status = status
            row.indexed_at = get_local_now()
            row.error_message = error

    async def run_indexing_task(self, target_folder_id: Optional[int] = None):
        """Legacy compatibility for callers outside the API; production uses index_worker."""
        return self.run_indexing_job(None, target_folder_id)


scanner = FileScanner()
