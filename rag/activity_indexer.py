"""Omni-RAG Activity Indexer: 將 SQLite 專案歷史與活動脈絡同步至 ChromaDB 與 BM25 知識庫。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config
from core.database import get_db
from core.models import (
    ActivityMicroSummary,
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
    OpenLoop,
    ProjectState,
    SecretaryNote,
)
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now
from rag.chunker import ChunkItem
from rag.retriever import bm25_service
from rag.vector_store import vector_store

logger = logging.getLogger("OmniContext.RAG.ActivityIndexer")

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
                if not matches_project(pr.project_key, pr.display_name):
                    continue
                content = (
                    f"【專案狀態】\n"
                    f"專案代碼: {pr.project_key}\n"
                    f"標準名稱: {pr.display_name}\n"
                    f"分類: {pr.category or '未分類'}\n"
                    f"最後動作摘要: {pr.last_action_summary or '無'}\n"
                    f"活躍狀態: {pr.status}\n"
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
                    section_title=f"專案狀態: {pr.display_name}",
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
                    f"置信度: {lp.confidence}\n"
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

            # 5. 小秘書記憶區筆記（ADR-012）：使用者筆記／偏好／決定與秘書觀察
            n_query = session.query(SecretaryNote).order_by(SecretaryNote.created_at.desc())
            if limit_per_type:
                n_query = n_query.limit(limit_per_type)
            for note in n_query.all():
                proj = note.project_key or "general"
                if not matches_project(proj, note.title):
                    continue
                label = {"user_note": "筆記", "preference": "偏好", "decision": "決定", "observation": "秘書觀察"}.get(
                    note.kind, note.kind
                )
                ts = note.created_at.isoformat() if note.created_at else ""
                content = (
                    f"【小秘書記憶區 · {label}】\n"
                    f"專案: {proj}\n"
                    f"標題: {note.title or '（無）'}\n"
                    f"內容:\n{note.body[:max_prompt_chars]}\n"
                    f"來源: {note.source}{(' · ' + note.source_ref) if note.source_ref else ''}\n"
                    f"時間: {ts or '未知'}"
                )
                chunks.append(ChunkItem(
                    chunk_id=f"act_note_{note.id}",
                    file_path=f"activity://{proj}/secretary_note/{note.id}",
                    filename=f"[{proj}] 🧠 {label}: {(note.title or note.body)[:30]}",
                    file_type=".secretary_note",
                    content=content,
                    chunk_index=0,
                    section_title=f"{label}: {(note.title or note.body)[:40]}",
                    metadata={
                        "source_domain": "activity",
                        "source_type": "secretary_note",
                        "note_kind": note.kind,
                        "project_key": proj,
                        "source_ref": f"secretary_notes:{note.id}",
                        "trust_status": "user_stated" if note.kind != "observation" else "derived_observation",
                        "timestamp": ts,
                    },
                ))

            # 6. 兩層摘要的 checkpoint 微摘要（已壓縮、不含 prompt/response 原文）
            m_query = session.query(ActivityMicroSummary).order_by(ActivityMicroSummary.period_start.desc())
            m_query = m_query.limit(limit_per_type or 400)
            for micro in m_query.all():
                if project_needle:
                    continue  # 微摘要不分專案；只在無專案過濾時納入
                start = micro.period_start.isoformat() if micro.period_start else ""
                end = micro.period_end.isoformat() if micro.period_end else ""
                content = (
                    f"【工作時段摘要】\n"
                    f"時段: {start[:16]} → {end[:16]}\n"
                    f"事件數: {micro.event_count}\n"
                    f"摘要:\n{micro.summary_text}"
                )
                chunks.append(ChunkItem(
                    chunk_id=f"act_micro_{micro.id}",
                    file_path=f"activity://general/micro_summary/{micro.id}",
                    filename=f"[general] 🕒 時段摘要 {start[:16]}",
                    file_type=".micro_summary",
                    content=content,
                    chunk_index=0,
                    section_title=f"時段摘要 {start[:16]}",
                    metadata={
                        "source_domain": "activity",
                        "source_type": "micro_summary",
                        "project_key": "general",
                        "source_ref": f"activity_micro_summaries:{micro.id}",
                        "trust_status": "local_llm_summary",
                        "timestamp": start,
                    },
                ))

        # 7. 既有報告檔（Handoff、同步報告、STATUS 草稿、每日入口、週/月報）
        if not project_needle:
            chunks.extend(self.build_report_chunks(limit_per_kind=limit_per_type or REPORT_FILES_PER_KIND))

        return chunks

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
                        "source_domain": "activity",
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
