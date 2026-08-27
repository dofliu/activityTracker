"""Omni-RAG Activity Indexer: 將 SQLite 專案歷史與活動脈絡同步至 ChromaDB 與 BM25 知識庫。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.database import get_db
from core.models import (
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
    OpenLoop,
    ProjectState,
)
from core.time_utils import get_local_now
from rag.chunker import ChunkItem
from rag.retriever import bm25_service
from rag.vector_store import vector_store

logger = logging.getLogger("OmniContext.RAG.ActivityIndexer")


class ActivityIndexer:
    def __init__(self):
        self._last_sync_receipt: Optional[Dict[str, Any]] = None

    def build_activity_chunks(
        self,
        database: Any | None = None,
        project_filter: Optional[str] = None,
        max_prompt_chars: int = 4000,
        limit_per_type: Optional[int] = None,
    ) -> List[ChunkItem]:
        """從 SQLite 資料庫讀取 AI 對話、Git 提交、專案狀態與 Open Loops 並轉為標準切片"""
        db = database or get_db()
        chunks: List[ChunkItem] = []
        project_needle = str(project_filter or "").strip().lower()

        def matches_project(*keys: Any) -> bool:
            if not project_needle:
                return True
            return any(project_needle in str(k or "").lower() for k in keys)

        with db.session_scope() as session:
            # 1. 專案狀態 (ProjectState)
            p_query = session.query(ProjectState)
            if limit_per_type:
                p_query = p_query.limit(limit_per_type)
            project_rows = p_query.all()

            for pr in project_rows:
                if not matches_project(pr.project_key, pr.canonical_name):
                    continue
                content = (
                    f"【專案狀態】\n"
                    f"專案代碼: {pr.project_key}\n"
                    f"標準名稱: {pr.canonical_name}\n"
                    f"分類: {pr.category or '未分類'}\n"
                    f"當前焦點: {pr.current_focus or '進行中'}\n"
                    f"專案摘要: {pr.summary or '無'}\n"
                    f"活躍狀態: {pr.status}\n"
                    f"未結事項數: {pr.open_loops_count}\n"
                    f"最後活動時間: {pr.last_activity_at.isoformat() if pr.last_activity_at else '未知'}"
                )
                ts = pr.last_activity_at.isoformat() if pr.last_activity_at else ""
                chunks.append(ChunkItem(
                    chunk_id=f"act_proj_{pr.project_key}",
                    file_path=f"activity://{pr.project_key}/project_state",
                    filename=f"[{pr.project_key}] 📌 專案狀態概覽",
                    file_type=".project_state",
                    content=content,
                    chunk_index=0,
                    section_title=f"專案狀態: {pr.canonical_name}",
                    metadata={
                        "source_domain": "activity",
                        "source_type": "project_state",
                        "project_key": pr.project_key,
                        "source_ref": f"project_states:{pr.project_key}",
                        "trust_status": "canonical_state",
                        "timestamp": ts
                    }
                ))

            # 2. 未結事項 (OpenLoop)
            l_query = session.query(OpenLoop).order_by(OpenLoop.last_seen_at.desc())
            if limit_per_type:
                l_query = l_query.limit(limit_per_type)
            loop_rows = l_query.all()

            for lp in loop_rows:
                proj = lp.project_key or "general"
                if not matches_project(proj):
                    continue
                content = (
                    f"【未結事項 / 待辦】\n"
                    f"所屬專案: {proj}\n"
                    f"事項標題: {lp.title}\n"
                    f"狀態: {lp.status}\n"
                    f"緊急度: {lp.urgency}\n"
                    f"解決備註: {lp.resolution_note or '尚未解決'}\n"
                    f"最後觀察時間: {lp.last_seen_at.isoformat() if lp.last_seen_at else '未知'}"
                )
                ts = lp.last_seen_at.isoformat() if lp.last_seen_at else ""
                chunks.append(ChunkItem(
                    chunk_id=f"act_loop_{lp.id}",
                    file_path=f"activity://{proj}/open_loop/{lp.id}",
                    filename=f"[{proj}] ⏳ 待辦事項: {lp.title[:30]}",
                    file_type=".open_loop",
                    content=content,
                    chunk_index=0,
                    section_title=f"待辦事項: {lp.title}",
                    metadata={
                        "source_domain": "activity",
                        "source_type": "open_loop",
                        "project_key": proj,
                        "source_ref": f"open_loops:{lp.id}",
                        "trust_status": lp.status,
                        "timestamp": ts
                    }
                ))

            # 3. AI 對話事件 (AIPromptEvent)
            ai_query = session.query(AIPromptEvent).order_by(AIPromptEvent.timestamp.desc())
            if limit_per_type:
                ai_query = ai_query.limit(limit_per_type)
            ai_rows = ai_query.all()

            for ai in ai_rows:
                proj = ai.project_tag or "general"
                if not matches_project(proj, ai.cwd):
                    continue
                prompt = (ai.prompt_text or "").strip()
                if len(prompt) < 2:
                    continue

                resp = (ai.response_text or "").strip()
                trust = str(ai.response_status or "legacy_unverified")
                content = f"【AI 對話紀錄 ({ai.platform or 'AI'})】\n專案: {proj}\n使用者提問:\n{prompt[:max_prompt_chars]}"
                if resp:
                    content += f"\n\nAI 回答 (候選):\n{resp[:max_prompt_chars]}"

                ts = ai.timestamp.isoformat() if ai.timestamp else ""
                platform_label = ai.platform.capitalize() if ai.platform else "AI"
                chunks.append(ChunkItem(
                    chunk_id=f"act_ai_{ai.id}",
                    file_path=f"activity://{proj}/ai_turn/{ai.id}",
                    filename=f"[{proj}] 🤖 {platform_label} 對話",
                    file_type=".ai_turn",
                    content=content,
                    chunk_index=0,
                    section_title=f"{platform_label} 對話 turn #{ai.id}",
                    metadata={
                        "source_domain": "activity",
                        "source_type": "ai_turn",
                        "project_key": proj,
                        "source_ref": f"ai_prompt_events:{ai.id}",
                        "trust_status": trust,
                        "timestamp": ts
                    }
                ))

            # 4. Git 提交 (GitActivityEvent)
            git_query = session.query(GitActivityEvent).order_by(GitActivityEvent.timestamp.desc())
            if limit_per_type:
                git_query = git_query.limit(limit_per_type)
            git_rows = git_query.all()

            for g in git_rows:
                repo = g.repo_name or "unknown"
                if not matches_project(repo, g.repo_path):
                    continue
                content = (
                    f"【Git Commit 紀錄】\n"
                    f"倉庫名稱: {repo}\n"
                    f"分支: {g.branch}\n"
                    f"Commit SHA: {g.commit_hash}\n"
                    f"提交訊息:\n{g.message}\n"
                    f"檔案異動: {g.files_changed_count} 檔 (+{g.insertions}/-{g.deletions})"
                )
                ts = g.timestamp.isoformat() if g.timestamp else ""
                short_sha = g.commit_hash[:8] if g.commit_hash else ""
                chunks.append(ChunkItem(
                    chunk_id=f"act_git_{g.id}",
                    file_path=f"activity://{repo}/git_commit/{g.id}",
                    filename=f"[{repo}] 🐙 Commit {short_sha}",
                    file_type=".git_commit",
                    content=content,
                    chunk_index=0,
                    section_title=f"Commit: {g.message[:40] if g.message else short_sha}",
                    metadata={
                        "source_domain": "activity",
                        "source_type": "git_commit",
                        "project_key": repo,
                        "source_ref": f"git_activity_events:{g.id}",
                        "trust_status": "git_observed",
                        "timestamp": ts
                    }
                ))

        return chunks

    def sync_all(self, database: Any | None = None, limit_per_type: Optional[int] = None) -> Dict[str, Any]:
        """全量/增量同步所有專案活動脈絡至 ChromaDB 與 BM25"""
        chunks = self.build_activity_chunks(database=database, limit_per_type=limit_per_type)
        if not chunks:
            receipt = {
                "status": "empty",
                "message": "資料庫中尚無可同步的專案活動紀錄",
                "total_activity_indexed": 0,
                "synced_at": get_local_now().isoformat()
            }
            self._last_sync_receipt = receipt
            return receipt

        # 1. 移除舊的 activity 領域切片
        try:
            vector_store.delete_by_source_domain("activity")
        except Exception as e:
            logger.warning(f"ChromaDB delete activity domain warning: {e}")

        try:
            bm25_service.delete_by_source_domain("activity")
        except Exception as e:
            logger.warning(f"BM25 delete activity domain warning: {e}")

        # 2. 寫入 ChromaDB
        vector_store.add_chunks(chunks)

        # 3. 寫入 BM25
        bm25_chunks = [
            {
                "chunk_id": c.chunk_id,
                "content": c.content,
                "metadata": {
                    "file_path": c.file_path,
                    "filename": c.filename,
                    "file_type": c.file_type,
                    "title": c.section_title,
                    **c.metadata
                }
            }
            for c in chunks
        ]
        bm25_service.add_or_update_chunks(bm25_chunks)

        # 4. 計算分類統計
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
            "total_activity_indexed": len(chunks),
            "type_counts": type_counts,
            "projects_count": len(projects_covered),
            "projects_covered": sorted(list(projects_covered)),
            "synced_at": get_local_now().isoformat()
        }
        self._last_sync_receipt = receipt
        logger.info(f"ActivityIndexer 同步完成: {len(chunks)} 筆切片, 涵蓋 {len(projects_covered)} 個專案")
        return receipt

    def get_status(self) -> Dict[str, Any]:
        """取得專案脈絡索引狀態"""
        if self._last_sync_receipt:
            return self._last_sync_receipt
        return {
            "status": "not_synced",
            "message": "專案活動脈絡尚未同步至 RAG 檢索庫",
            "total_activity_indexed": 0,
            "synced_at": None
        }


activity_indexer = ActivityIndexer()
