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
        except Exception:
            return ""

    def scan_folder_files(self, folder_path: str) -> List[Dict[str, Any]]:
        found_files = []
        folder_p = Path(folder_path).resolve()
        if not folder_p.exists():
            return found_files

        for root, dirs, files in os.walk(folder_p):
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

    async def run_indexing_task(self, target_folder_id: Optional[int] = None):
        async with self._lock:
            if progress.is_running:
                return
            progress.is_running = True
            progress.status = "scanning"
            progress.start_time = time.time()
            progress.processed_files = 0
            progress.indexed_chunks = 0
            progress.error_count = 0
            progress.logs.clear()
            progress.log("開始掃描知識庫目錄檔案...")

            database = get_db()

            try:
                folders_to_scan = []
                with database.session_scope() as session:
                    query = session.query(RAGIndexedFolder).filter(RAGIndexedFolder.is_active == 1)
                    if target_folder_id:
                        query = query.filter(RAGIndexedFolder.id == target_folder_id)
                    folder_rows = query.all()
                    folders_to_scan = [{"id": f.id, "path": f.path} for f in folder_rows]

                if not folders_to_scan:
                    progress.log("未設定或無啟用的資料夾目錄")
                    progress.status = "completed"
                    progress.is_running = False
                    return

                all_scanned_files = []
                for fld in folders_to_scan:
                    f_id, f_path = fld["id"], fld["path"]
                    progress.log(f"正在掃描目錄: {f_path}")
                    disk_files = self.scan_folder_files(f_path)
                    for df in disk_files:
                        df["folder_id"] = f_id
                    all_scanned_files.extend(disk_files)

                    total_size = sum(f["size"] for f in disk_files)
                    with database.session_scope() as session:
                        folder_row = session.query(RAGIndexedFolder).filter_by(id=f_id).first()
                        if folder_row:
                            folder_row.file_count = len(disk_files)
                            folder_row.total_size = total_size
                            folder_row.last_scanned_at = get_local_now()

                progress.total_files = len(all_scanned_files)
                progress.log(f"掃描完成，共發現 {progress.total_files} 個待索引檔案")
                progress.status = "indexing"

                # Check for deleted files
                current_paths = {f["path"] for f in all_scanned_files}
                with database.session_scope() as session:
                    existing_db_files = session.query(RAGIndexedFile).all()
                    deleted_paths = [row.path for row in existing_db_files if row.path not in current_paths]
                    for dp in deleted_paths:
                        progress.log(f"偵測到檔案已刪除，清理索引: {Path(dp).name}")
                        vector_store.delete_by_file_path(dp)
                        bm25_service.delete_by_file_path(dp)
                        session.query(RAGIndexedFile).filter_by(path=dp).delete()

                # Process files (Incremental Check)
                all_bm25_new_chunks = []

                for idx, file_info in enumerate(all_scanned_files):
                    f_path = file_info["path"]
                    f_name = file_info["filename"]
                    f_ext = file_info["extension"]
                    f_size = file_info["size"]
                    f_mtime = file_info["mtime"]
                    f_folder_id = file_info["folder_id"]

                    progress.current_file = f_name
                    progress.processed_files = idx + 1

                    f_hash = self._compute_file_hash(f_path)

                    with database.session_scope() as session:
                        db_file = session.query(RAGIndexedFile).filter_by(path=f_path).first()
                        if db_file and db_file.file_hash == f_hash and db_file.status == "indexed":
                            continue

                    # File is new or changed -> Parse & Chunk
                    progress.log(f"正在解析並建立切片: {f_name}")
                    try:
                        parsed_doc = parser_hub.parse_file(f_path)
                        chunks = chunker.chunk_document(parsed_doc)

                        # Clean old chunks if modified
                        vector_store.delete_by_file_path(f_path)
                        bm25_service.delete_by_file_path(f_path)

                        if chunks:
                            vector_store.add_chunks(chunks)
                            chunk_dicts = [
                                {
                                    "chunk_id": c.chunk_id,
                                    "content": c.content,
                                    "metadata": {
                                        "file_path": c.file_path,
                                        "filename": c.filename,
                                        "file_type": c.file_type,
                                        "page": c.page_number,
                                        "slide": c.slide_number,
                                        "sheet": c.sheet_name,
                                        "title": c.section_title
                                    }
                                }
                                for c in chunks
                            ]
                            all_bm25_new_chunks.extend(chunk_dicts)
                            progress.indexed_chunks += len(chunks)

                        with database.session_scope() as session:
                            row = session.query(RAGIndexedFile).filter_by(path=f_path).first()
                            if row is None:
                                row = RAGIndexedFile(path=f_path, filename=f_name, extension=f_ext)
                                session.add(row)
                            row.folder_id = f_folder_id
                            row.filename = f_name
                            row.extension = f_ext
                            row.file_size = f_size
                            row.last_modified = f_mtime
                            row.file_hash = f_hash
                            row.chunk_count = len(chunks)
                            row.status = "indexed"
                            row.indexed_at = get_local_now()
                            row.error_message = None

                    except Exception as e:
                        progress.error_count += 1
                        err_msg = str(e)
                        progress.log(f"解析異常: {f_name} ({err_msg})")
                        with database.session_scope() as session:
                            row = session.query(RAGIndexedFile).filter_by(path=f_path).first()
                            if row is None:
                                row = RAGIndexedFile(path=f_path, filename=f_name, extension=f_ext)
                                session.add(row)
                            row.folder_id = f_folder_id
                            row.filename = f_name
                            row.extension = f_ext
                            row.file_size = f_size
                            row.last_modified = f_mtime
                            row.file_hash = f_hash
                            row.chunk_count = 0
                            row.status = "failed"
                            row.indexed_at = get_local_now()
                            row.error_message = err_msg

                    await asyncio.sleep(0.001)

                if all_bm25_new_chunks:
                    bm25_service.add_or_update_chunks(all_bm25_new_chunks)

                progress.status = "completed"
                progress.log(f"索引作業全部完成！目前向量庫累積 {vector_store.count()} 個切片")
            except Exception as e:
                progress.status = "error"
                progress.log(f"索引任務發生錯誤: {str(e)}")
            finally:
                progress.is_running = False
                progress.elapsed_seconds = round(time.time() - progress.start_time, 1)


scanner = FileScanner()
