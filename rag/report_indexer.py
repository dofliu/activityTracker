"""DeskRAG 報告索引：只索引秘書自己寫出的報告檔（ADR-023，TODO D7）。

2026-09-16 之前這個模組叫 `activity_indexer`，除了報告檔以外還把七種活動實體
（AI turn、commit、專案狀態、未結事項、檔案事件、秘書筆記、時段微摘要）再
embedding 一次，和 `core/semantic_index` 各存一份——同一批活動算兩次、兩邊對
「活動」的定義還不一樣，而且沒裝 `[rag]` extra 的安裝等於少一半記憶。

ADR-023 把活動記憶收回核心索引（`core/semantic_index`，零重依賴、每筆只算一次），
這裡只剩**文件**：`exporters.reports_dir` 底下秘書產出的 markdown（白名單不變）。
:meth:`ReportIndexer.sync_all` 另外負責把舊的 `source_domain == "activity"` 切片
一次性清掉——那些是重複資料，留著只會讓對話引用到過時的第二份活動記憶。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import get_config
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now
from rag.chunker import ChunkItem
from rag.retriever import bm25_service
from rag.vector_store import vector_store

logger = logging.getLogger("OmniContext.RAG.ReportIndexer")

REPORT_DOMAIN = "report"          # 秘書寫出的報告檔；使用者資料夾的檔案是 "document"
LEGACY_ACTIVITY_DOMAIN = "activity"  # ADR-023 之前的活動切片，只清不寫

# 只讀秘書自己寫出的報告子目錄（相對 exporters.reports_dir）；不掃使用者任意資料夾。
REPORT_KINDS: tuple[tuple[str, str, str], ...] = (
    ("handoffs", "Context Handoff", "report_handoff"),
    ("repo_sync", "Repo 同步報告", "report_repo_sync"),
    ("status_drafts", "STATUS 維護草稿", "report_status_draft"),
    ("", "每日入口與週／月報", "report_daily_entry"),
)
# reports_dir 根目錄只認這幾種秘書自己寫的檔名，不掃使用者放進去的其他 markdown。
ROOT_REPORT_PREFIXES = ("OMNICONTEXT_TODAY", "Weekly_Rollup_", "Monthly_Rollup_")
REPORT_FILES_PER_KIND = 30
REPORT_MAX_CHARS = 6000


def _project_from_report_name(sub: str, stem: str) -> Optional[str]:
    """``Handoff_<project>_<stamp>`` / ``RepoSync_<date>`` 這類檔名裡的專案鍵；取不到就 None。"""
    if sub == "handoffs" and stem.startswith("Handoff_"):
        parts = stem[len("Handoff_"):].split("_")
        while len(parts) > 1 and parts[-1].isdigit():
            parts.pop()  # 去掉尾端的日期／時間戳
        key = "_".join(parts)
        return key or None
    return None


ACTIVITY_MEMORY_BOUNDARY = (
    "知識庫只收文件（使用者資料夾與秘書寫出的報告檔）；活動記憶在 core/semantic_index，"
    "每筆活動只 embedding 一次（ADR-023）。"
)


class ReportIndexer:
    """把秘書寫出的報告檔同步進 DeskRAG；活動記憶不在這裡（ADR-023）。"""

    def __init__(self):
        self._last_sync_receipt: Optional[Dict[str, Any]] = None

    def build_report_chunks(
        self,
        *,
        cfg: Any | None = None,
        limit_per_kind: int = 30,
        max_chars: int = REPORT_MAX_CHARS,
    ) -> List[ChunkItem]:
        """把 ``exporters.reports_dir`` 下秘書產出的 markdown 報告讀成切片。

        只讀本專案自己寫出的報告子目錄（白名單），每類只取最新 N 份、每份截到
        ``max_chars``；chunk_id 由相對路徑雜湊而成，重跑會覆蓋同一份。
        """
        cfg = cfg or get_config()
        base = resolve_runtime_path(cfg.get("exporters.reports_dir", "reports"))
        chunks: List[ChunkItem] = []
        for sub, label, source_type in REPORT_KINDS:
            folder = base / sub if sub else base
            if not folder.is_dir():
                continue
            try:
                files = sorted(
                    (p for p in folder.glob("*.md") if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[: max(1, limit_per_kind)]
            except OSError:
                continue
            for path in files:
                if sub == "" and not path.stem.startswith(ROOT_REPORT_PREFIXES):
                    continue  # 根目錄只認每日入口檔與週／月報
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                text = text.strip()
                if not text:
                    continue
                truncated = len(text) > max_chars
                body = text[:max_chars] + ("\n…（已截斷）" if truncated else "")
                rel = str(path.relative_to(base)).replace("\\", "/")
                digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
                mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
                project_key = _project_from_report_name(sub, path.stem) or "general"
                chunks.append(ChunkItem(
                    chunk_id=f"act_report_{digest}",
                    file_path=str(path),
                    filename=f"[{project_key}] 📄 {label}: {path.stem}",
                    file_type=".md",
                    content=f"【{label}】\n檔案: {rel}\n更新: {mtime}\n\n{body}",
                    chunk_index=0,
                    section_title=f"{label}: {path.stem}",
                    metadata={
                        "source_domain": "report",
                        "source_type": "report_rollup" if path.stem.endswith("Rollup") or "_Rollup_" in path.stem else source_type,
                        "report_kind": sub or ("rollup" if "_Rollup_" in path.stem else "daily_entry"),
                        "project_key": project_key,
                        "source_ref": f"report_file:{rel}",
                        "trust_status": "derived_report",
                        "timestamp": mtime,
                        "truncated": truncated,
                    },
                ))
        return chunks

    def purge_legacy_activity_chunks(self) -> int:
        """清掉 ADR-023 之前寫進 RAG 的活動切片，回傳清掉幾筆（清不到就回 0）。"""
        removed = 0
        try:
            removed = bm25_service.count_by_source_domain(LEGACY_ACTIVITY_DOMAIN)
            bm25_service.delete_by_source_domain(LEGACY_ACTIVITY_DOMAIN)
        except Exception as exc:  # noqa: BLE001 — 清不掉不該讓報告同步失敗
            logger.warning("BM25 legacy activity purge warning: %s", exc)
        try:
            vector_store.delete_by_source_domain(LEGACY_ACTIVITY_DOMAIN)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaDB legacy activity purge warning: %s", exc)
        return removed

    def sync_all(self, database: Any | None = None, limit_per_type: Optional[int] = None) -> Dict[str, Any]:
        """同步報告檔到 Chroma 與 BM25，並清掉舊的活動切片。

        ``database`` 只為了沿用既有呼叫端的簽章而保留——報告來自檔案系統，
        這個函式不再讀任何 SQLite 業務資料（活動記憶見 `core/semantic_index`）。
        """
        legacy_removed = self.purge_legacy_activity_chunks()
        chunks = self.build_report_chunks(limit_per_kind=limit_per_type or REPORT_FILES_PER_KIND)

        # 舊的報告切片一併清掉，檔案刪了就不該繼續被引用
        try:
            vector_store.delete_by_source_domain(REPORT_DOMAIN)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaDB delete report domain warning: %s", exc)
        try:
            bm25_service.delete_by_source_domain(REPORT_DOMAIN)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 delete report domain warning: %s", exc)

        if not chunks:
            receipt = {
                "status": "empty",
                "message": "尚未產出任何秘書報告檔（Handoff／同步報告／STATUS 草稿／每日入口）",
                "total_reports_indexed": 0,
                "legacy_activity_chunks_removed": legacy_removed,
                "synced_at": get_local_now().isoformat(),
                "claim_boundary": ACTIVITY_MEMORY_BOUNDARY,
            }
            self._last_sync_receipt = receipt
            return receipt

        vector_store.add_chunks(chunks)
        bm25_service.add_or_update_chunks([
            {
                "chunk_id": c.chunk_id,
                "content": c.content,
                "metadata": {
                    "file_path": c.file_path,
                    "filename": c.filename,
                    "file_type": c.file_type,
                    "title": c.section_title,
                    **c.metadata,
                },
            }
            for c in chunks
        ])

        type_counts: Dict[str, int] = {}
        projects_covered = set()
        for c in chunks:
            stype = c.metadata.get("source_type", "unknown")
            type_counts[stype] = type_counts.get(stype, 0) + 1
            pkey = c.metadata.get("project_key")
            if pkey:
                projects_covered.add(pkey)

        receipt = {
            "status": "success",
            "total_reports_indexed": len(chunks),
            "legacy_activity_chunks_removed": legacy_removed,
            "type_counts": type_counts,
            "projects_count": len(projects_covered),
            "projects_covered": sorted(projects_covered),
            "synced_at": get_local_now().isoformat(),
            "claim_boundary": ACTIVITY_MEMORY_BOUNDARY,
        }
        self._last_sync_receipt = receipt
        logger.info(
            "ReportIndexer 同步完成：%s 份報告，另清掉 %s 筆舊活動切片",
            len(chunks), legacy_removed,
        )
        return receipt

    def get_status(self) -> Dict[str, Any]:
        if self._last_sync_receipt:
            return self._last_sync_receipt
        return {
            "status": "not_synced",
            "message": "秘書報告檔尚未同步至知識庫",
            "total_reports_indexed": 0,
            "synced_at": None,
            "claim_boundary": ACTIVITY_MEMORY_BOUNDARY,
        }


report_indexer = ReportIndexer()
