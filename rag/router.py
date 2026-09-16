import os
import sys
import uuid
import json
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import get_config
from core.database import get_db
from core.models import RAGIndexedFolder, RAGIndexedFile, RAGChatSession, RAGChatMessage
from core.platform_services import open_local_path
from core.time_utils import get_local_now
from rag.config import rag_settings
from rag import availability
from rag.availability import RagExtraNotInstalled
from rag.jobs import (
    create_job, get_job, get_latest_job, launch_worker, request_cancel,
    request_pause, update_job,
)
from rag.storage import chroma_report, storage_report
from rag.retrieval.base import CitationSource
from rag.retrieval.catalog import DEFAULT_STRATEGY, STRATEGY_CATALOG
from rag.retrieval.context import citations_from_payload, format_context_prompt
from rag.retrieval_client import (
    RetrievalTimeoutError, retrieval_client, retrieval_mode,
)

RETRIEVAL_TIMEOUT_SECONDS = 60
MEMORY_CONTEXT_TIMEOUT_SECONDS = 8  # 記憶區只讀 SQLite；超過就不帶脈絡照常回答


logger = logging.getLogger("OmniContext.RAG.Router")
router = APIRouter(prefix="/api/v1/rag", tags=["DeskRAG Knowledge & Chat"])


# Request Models
class AddFolderRequest(BaseModel):
    path: str
    name: Optional[str] = None
    max_files: Optional[int] = None
    throttle_ms: Optional[int] = None


class ScanRequest(BaseModel):
    folder_id: Optional[int] = None
    max_files: Optional[int] = None
    throttle_ms: Optional[int] = None


class ConfirmIndexRemovalRequest(BaseModel):
    confirm: bool = False


def _chat_target(provider: Optional[str], model: Optional[str]) -> tuple[str, Optional[str]]:
    """知識庫對話的 provider／model 預設：`rag.active_provider`／`rag.active_model`（Ollama 才有預設模型）。

    以前寫在 rag/llm_gateway.py 裡；D2 之後 LLM client 只有一份，不認識 rag 的設定鍵，
    所以由這裡（呼叫端）決定要問哪一家、哪個模型。
    """
    from core.llm_client import normalize_provider

    cfg = get_config()
    prov = normalize_provider(provider or cfg.get("rag.active_provider", "ollama"), cfg)
    if not model and prov == "ollama":
        model = cfg.get("rag.active_model") or None
    return prov, (model or None)


def _extra_missing_detail(exc: RagExtraNotInstalled) -> Dict[str, Any]:
    """503 的 detail 是機器可讀的：前端靠 error 欄位判斷，不靠比對中文句子。"""
    return {
        "error": "rag_extra_not_installed",
        "missing": exc.missing,
        "install_hint": availability.INSTALL_HINT,
        "message": str(exc),
    }


def _start_job_or_raise(job_type: str, folder_id: Optional[int] = None, max_files: Optional[int] = None, throttle_ms: Optional[int] = None):
    try:
        job = create_job(job_type, folder_id=folder_id, max_files=max_files, throttle_ms=throttle_ms)
        return launch_worker(job["id"])
    except RagExtraNotInstalled as exc:
        raise HTTPException(status_code=503, detail=_extra_missing_detail(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        if "job" in locals():
            update_job(job["id"], status="failed", error_message=str(exc), message="worker 無法啟動")
        raise HTTPException(status_code=500, detail=f"無法啟動 RAG worker: {exc}") from exc


class OpenFileRequest(BaseModel):
    path: str


class MessageItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    messages: List[MessageItem]
    provider: Optional[str] = None
    model: Optional[str] = None
    enable_rag: bool = True
    retrieval_strategy: Optional[str] = None
    top_k: Optional[int] = None
    hybrid_alpha: Optional[float] = None
    score_threshold: Optional[float] = None
    custom_system_prompt: Optional[str] = None
    # ADR-012：把小秘書記憶區（今日狀態、top 提案、筆記）注入 system prompt；預設開，可關。
    include_memory: bool = True


class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None
    title: Optional[str] = "新對話"


class SaveMessageRequest(BaseModel):
    session_id: str
    role: str
    content: str
    citations: Optional[List[Dict[str, Any]]] = None
    provider: Optional[str] = None
    model: Optional[str] = None


# Endpoints
@router.get("/folders")
def list_folders():
    db = get_db()
    with db.session_scope() as session:
        rows = session.query(RAGIndexedFolder).order_by(RAGIndexedFolder.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "path": r.path,
                "name": r.name,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "last_scanned_at": r.last_scanned_at.isoformat() if r.last_scanned_at else None,
                "file_count": r.file_count,
                "total_size": r.total_size
            }
            for r in rows
        ]


@router.post("/folders")
def add_folder(req: AddFolderRequest):
    p = Path(req.path).resolve()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"目錄不存在或非資料夾: {req.path}")

    folder_path = str(p)
    folder_name = req.name or p.name or str(p)

    db = get_db()
    with db.session_scope() as session:
        existing = session.query(RAGIndexedFolder).filter_by(path=folder_path).first()
        if existing:
            folder_id = existing.id
            existing.name = folder_name
            existing.is_active = 1
        else:
            new_folder = RAGIndexedFolder(
                path=folder_path,
                name=folder_name,
                is_active=1,
                created_at=get_local_now()
            )
            session.add(new_folder)
            session.flush()
            folder_id = new_folder.id

    job = _start_job_or_raise("index", folder_id, req.max_files, req.throttle_ms)
    return {
        "success": True,
        "folder_id": folder_id,
        "path": folder_path,
        "job": job,
        "message": "目錄已成功加入，已交由獨立 RAG worker 建立索引。"
    }


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, confirm: bool = False):
    """保留舊路徑，但沒有 confirm 就不會刪除任何索引。"""
    if not confirm:
        raise HTTPException(status_code=400, detail="請改用確認式 POST /folders/{id}/remove-index")
    return remove_folder_index(folder_id, ConfirmIndexRemovalRequest(confirm=True))


@router.post("/folders/{folder_id}/remove-index")
def remove_folder_index(folder_id: int, req: ConfirmIndexRemovalRequest):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="此操作需要 confirm=true；原始來源檔案不會被刪除。")
    db = get_db()
    with db.session_scope() as session:
        fld = session.query(RAGIndexedFolder).filter_by(id=folder_id).first()
        if not fld:
            raise HTTPException(status_code=404, detail="找不到指定的資料夾")
        folder_path = fld.path
    job = _start_job_or_raise("remove_folder", folder_id)
    return {
        "success": True,
        "job": job,
        "message": f"已確認移除 {folder_path} 的索引；原始檔案不會被刪除。",
    }


@router.post("/scan")
def trigger_scan(req: ScanRequest = ScanRequest()):
    job = _start_job_or_raise("index", req.folder_id, req.max_files, req.throttle_ms)
    return {"success": True, "job": job, "message": "已交由獨立 RAG worker 掃描與索引。"}


@router.post("/clear-index")
def clear_all_index(req: ConfirmIndexRemovalRequest):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="此操作需要 confirm=true；僅清空 RAG 索引，不會刪除來源檔或對話。")
    job = _start_job_or_raise("clear_all")
    return {
        "success": True,
        "job": job,
        "message": "已確認清空全部 RAG 索引；原始來源檔與對話紀錄不會被刪除。",
    }


@router.get("/jobs/current")
def current_job():
    return get_latest_job() or {"status": "idle", "is_running": False}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到指定 RAG 工作")
    return job


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str):
    job = request_pause(job_id, True)
    if not job:
        raise HTTPException(status_code=404, detail="找不到指定 RAG 工作")
    return job


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str):
    job = request_pause(job_id, False)
    if not job:
        raise HTTPException(status_code=404, detail="找不到指定 RAG 工作")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = request_cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到指定 RAG 工作")
    return job


@router.get("/storage")
def rag_storage():
    return storage_report()


@router.post("/storage/verify")
def rag_storage_verify():
    job = _start_job_or_raise("audit")
    return {"success": True, "job": job, "message": "已交由獨立 worker 驗證 Chroma、BM25 與 SQLite 一致性。"}


@router.get("/storage/chroma")
def rag_storage_chroma():
    """Chroma 目錄的空間帳：活的片段、刪掉沒回收的孤兒、SQLite 空頁。只讀。"""
    return chroma_report()


@router.post("/storage/compact-chroma")
def rag_compact_chroma(req: ConfirmIndexRemovalRequest):
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="此操作需要 confirm=true；只會刪除不被引用的 Chroma 片段目錄並 VACUUM，不動來源檔、對話或現有索引。",
        )
    # 檢索 worker 會把片段檔案開著（Windows 上刪不掉），先請它讓開；
    # 下一次提問或按預熱會自動重新啟動（ADR-009 Addendum B 之後，明示預熱一律重載）。
    retrieval_released = None
    try:
        retrieval_released = retrieval_client.shutdown().get("state")
    except Exception as exc:  # noqa: BLE001 — 放不掉就照常進行，失敗會如實記在收據裡
        retrieval_released = f"error:{type(exc).__name__}"
    job = _start_job_or_raise("compact_chroma")
    return {
        "success": True,
        "job": job,
        "retrieval_worker": retrieval_released,
        "message": "已交由獨立 worker 回收 Chroma 空間；只刪不被引用的片段目錄，現有索引不受影響。",
    }


@router.post("/storage/rebuild-bm25")
def rag_rebuild_bm25():
    job = _start_job_or_raise("rebuild_bm25")
    return {"success": True, "job": job, "message": "已交由獨立 worker 從 Chroma 重建 BM25，不會重新掃描來源檔案。"}


@router.post("/memory/sync")
def rag_memory_sync():
    """把秘書寫出的報告檔（Handoff、同步報告、STATUS 草稿、每日入口）同步進知識庫。

    ADR-023（TODO D7）起，筆記／微摘要／事件這些**活動記憶**不再進 RAG——它們在
    `core/semantic_index` 只 embedding 一次，對話要引用時查的是同一份。這個 job 因此
    只做文件側的事，外加清掉舊版寫進 RAG 的重複活動切片。跑在獨立 worker。
    """
    job = _start_job_or_raise("activity_sync")
    return {
        "success": True,
        "job": job,
        "message": "已交由獨立 worker 同步秘書報告檔，並清掉舊版重複的活動切片。",
    }


@router.get("/progress")
def get_progress():
    data = get_latest_job() or {
        "status": "idle", "is_running": False, "progress_percent": 0,
        "processed_files": 0, "total_files": 0, "indexed_chunks": 0,
        "current_file": "", "elapsed_seconds": 0, "error_count": 0,
    }
    # SQLite summary is lightweight; direct Chroma count is deliberately worker-only.
    data["total_indexed_chunks"] = storage_report()["source_chunks"]
    return data


@router.get("/files")
def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = None
):
    db = get_db()
    with db.session_scope() as session:
        query = session.query(RAGIndexedFile)
        if search:
            query = query.filter(
                (RAGIndexedFile.filename.like(f"%{search}%")) |
                (RAGIndexedFile.path.like(f"%{search}%"))
            )
        if status:
            query = query.filter(RAGIndexedFile.status == status)

        total_count = query.count()
        offset = (page - 1) * page_size
        rows = query.order_by(RAGIndexedFile.indexed_at.desc()).offset(offset).limit(page_size).all()

        return {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": r.id,
                    "folder_id": r.folder_id,
                    "path": r.path,
                    "filename": r.filename,
                    "extension": r.extension,
                    "file_size": r.file_size,
                    "last_modified": r.last_modified,
                    "chunk_count": r.chunk_count,
                    "status": r.status,
                    "error_message": r.error_message,
                    "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None
                }
                for r in rows
            ]
        }


@router.post("/open-file")
def open_file_in_explorer(req: OpenFileRequest):
    p = Path(req.path).resolve()
    if not p.exists():
        raise HTTPException(status_code=404, detail="檔案不存在")

    res = open_local_path(str(p), select=True)
    if not res.get("success", False):
        raise HTTPException(status_code=500, detail=res.get("message", "無法開啟檔案"))
    return res


@router.get("/file-content")
def get_file_content(path: str):
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="檔案不存在或非檔案")

    try:
        from rag.parsers.parser_hub import parser_hub
        doc = parser_hub.parse_file(str(p))
        return {
            "filename": doc.filename,
            "file_path": doc.file_path,
            "file_type": doc.file_type,
            "total_sections": len(doc.sections),
            "sections": [s.model_dump() for s in doc.sections[:50]]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析檔案失敗: {str(e)}")


@router.get("/strategies")
def list_strategies():
    # 靜態目錄：主服務不必為了下拉選單 import registry（那會在主程序建立 Chroma client）。
    return {
        "default": DEFAULT_STRATEGY,
        "strategies": list(STRATEGY_CATALOG),
    }


@router.get("/retrieval/status")
def retrieval_worker_status():
    """常駐檢索 worker 的程序狀態與預熱收據（記憶體內狀態，不宣稱檢索正確性）。"""
    return retrieval_client.status()


@router.post("/retrieval/warmup")
def retrieval_worker_warmup():
    """在背景把索引載進檢索 worker；主服務不等待、也不載入任何索引。"""
    if retrieval_mode() != "worker":
        raise HTTPException(status_code=409, detail="目前為 in_process 檢索模式，沒有可預熱的 worker")
    # 使用者明示按下預熱：一律重載，否則建完索引後只會拿到舊計數（ADR-009 Addendum B）
    receipt = retrieval_client.warmup_in_background(reason="dashboard", force=True)
    if isinstance(receipt, dict) and receipt.get("state") == "unavailable":
        # 沒裝 [rag] extra：client 不會啟動子程序；回機器可讀的 503 讓前端說要裝什麼（TODO D1）
        missing = list(receipt.get("extra_missing") or availability.missing_index_packages())
        raise HTTPException(status_code=503, detail=_extra_missing_detail(RagExtraNotInstalled(missing)))
    return receipt


@router.post("/retrieval/shutdown")
def retrieval_worker_shutdown():
    """釋放檢索 worker 佔用的記憶體；下一次提問會自動重新啟動。"""
    return retrieval_client.shutdown()


ACTIVITY_CITATION_TOP_K = 3


def _activity_citations(query: str, start_index: int, top_k: int = ACTIVITY_CITATION_TOP_K):
    """活動脈絡來自**核心** semantic index（ADR-023，TODO D7）。

    以前這些切片和文件一起躺在 Chroma 裡（`rag/activity_indexer` 再 embedding 一次），
    於是 `omni ask` 與這裡引用的是兩份會漂移的活動記憶。現在兩邊查同一份；沒有索引、
    Ollama 沒開或索引是空的，就只少了活動段——文件段照常，並如實留下一行 log。
    """
    from core.semantic_index import semantic_search

    try:
        result = semantic_search(query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — 活動段拿不到不該讓整段對話失敗
        logger.info("Activity context unavailable (%s); answering with documents only.", type(exc).__name__)
        return []

    citations = []
    for offset, item in enumerate(result.get("sources") or []):
        source_ref = str(item.get("source_ref") or "")
        citations.append(CitationSource(
            index=start_index + offset,
            chunk_id=f"activity:{source_ref}",
            file_path=source_ref,
            filename=f"[{item.get('project_key') or 'general'}] 🧠 {item.get('title') or source_ref}",
            file_type=f".{item.get('source_type') or 'activity'}",
            title=item.get("title"),
            content=str(item.get("excerpt") or ""),
            score=float(item.get("score") or 0.0),
            retrieval_type="semantic_index",
            source_domain="activity",
            source_type=item.get("source_type"),
            project_key=item.get("project_key"),
            source_ref=source_ref,
            timestamp=item.get("source_updated_at"),
            trust_status=item.get("trust_status"),
        ))
    return citations


def _retrieve_citations(query: str, req: "ChatRequest"):
    """依設定把檢索送進常駐 worker（預設）或在本程序執行，再補上活動脈絡。

    文件段兩條路徑都回傳 CitationSource 清單；worker 路徑的逾時由 client 自己
    處理（逾時即 kill 並在下次重啟），這裡把它轉成 asyncio.TimeoutError
    讓上層沿用同一段降級邏輯。活動段一律來自核心 semantic index（ADR-023）。
    """
    documents = _retrieve_documents(query, req)
    activity = _activity_citations(query, start_index=len(documents) + 1)
    return list(documents) + activity


def _retrieve_documents(query: str, req: "ChatRequest"):
    """文件段：使用者資料夾與秘書寫出的報告檔（DeskRAG，需要 `[rag]` extra）。"""
    if retrieval_mode() == "worker":
        if retrieval_client.status().get("state") == "unavailable":
            # 沒裝 [rag] extra：對話照常，只是不帶文件脈絡；狀態端點會說明缺什麼（TODO D1）。
            logger.info("RAG retrieval skipped: rag extra not installed (%s)", availability.INSTALL_HINT)
            return []
        try:
            payload = retrieval_client.retrieve(
                query=query,
                strategy=req.retrieval_strategy,
                top_k=req.top_k,
                alpha=req.hybrid_alpha,
                score_threshold=req.score_threshold or 0.0,
                timeout=RETRIEVAL_TIMEOUT_SECONDS,
            )
        except RetrievalTimeoutError as exc:
            raise asyncio.TimeoutError(str(exc)) from exc
        return citations_from_payload(payload)

    if availability.missing_index_packages():
        logger.info("RAG retrieval skipped (in_process): rag extra not installed (%s)", availability.INSTALL_HINT)
        return []
    from rag.retrieval.registry import retriever_registry
    return retriever_registry.retrieve(
        query=query,
        strategy=req.retrieval_strategy,
        top_k=req.top_k,
        alpha=req.hybrid_alpha,
        score_threshold=req.score_threshold or 0.0,
    )


@router.post("/chat")
async def chat_stream(req: ChatRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="對話訊息不可為空")

    last_user_message = req.messages[-1].content
    base_sys_prompt = req.custom_system_prompt or rag_settings.DEFAULT_SYSTEM_PROMPT
    llm_msgs = [{"role": m.role, "content": m.content} for m in req.messages]

    # 記憶區只讀 SQLite 與既有收據，不載入索引套件；失敗一律降級為「沒有記憶脈絡」。
    memory_text = ""
    memory_receipt: Dict[str, Any] = {"included": False, "reason": "not_requested"}
    if getattr(req, "include_memory", True):
        try:
            from core.secretary_memory import chat_context_enabled, memory_context

            if chat_context_enabled():
                memory = await asyncio.wait_for(asyncio.to_thread(memory_context), timeout=MEMORY_CONTEXT_TIMEOUT_SECONDS)
                memory_text = memory["text"]
                memory_receipt = memory["receipt"]
            else:
                memory_receipt = {"included": False, "reason": "disabled"}
        except asyncio.TimeoutError:
            memory_receipt = {"included": False, "reason": "timeout"}
        except Exception as exc:  # noqa: BLE001 — 記憶區故障不得中止對話
            logger.warning("secretary memory context unavailable: %s", type(exc).__name__)
            memory_receipt = {"included": False, "reason": f"error:{type(exc).__name__}"}

    async def event_generator():
        """SSE 生命週期契約：無論檢索或 LLM 發生什麼事，一定送出 done。

        瀏覽器的「回覆中」狀態只會由 done（或連線中斷）解除，因此任何
        沒有收尾的例外都會讓介面永遠卡住。這裡把三個階段都包起來：
        檢索（含硬性逾時）、串流、收尾，失敗一律轉成可讀的訊息事件。
        """
        from core.llm_client import llm_client

        chat_provider, chat_model = _chat_target(req.provider, req.model)

        citations = []
        context_text = ""
        try:
            # 先送一個 status，讓瀏覽器立刻收到位元組：worker 尚未預熱時，
            # 首次檢索仍要在子程序載入 Chroma/BM25，沒有這個事件會被誤認為沒回應。
            yield "event: status\ndata: {\"stage\": \"retrieving\"}\n\n"

            if req.enable_rag:
                # worker 模式由 client 在 RETRIEVAL_TIMEOUT_SECONDS 到時 kill 子程序並拋出，
                # 外層多給幾秒寬限讓那個例外傳回來；in_process 模式外層就是唯一的守門。
                grace = 5 if retrieval_mode() == "worker" else 0
                try:
                    citations = await asyncio.wait_for(
                        asyncio.to_thread(_retrieve_citations, last_user_message, req),
                        timeout=RETRIEVAL_TIMEOUT_SECONDS + grace,
                    )
                    context_text = format_context_prompt(citations)
                except asyncio.TimeoutError:
                    logger.warning("RAG retrieval timed out after %ss", RETRIEVAL_TIMEOUT_SECONDS)
                    citations = []
                    context_text = (
                        f"（知識庫檢索超過 {RETRIEVAL_TIMEOUT_SECONDS} 秒未完成，"
                        "本次回答不使用文件脈絡）"
                    )
                except Exception as e:  # noqa: BLE001 — 檢索失敗不應中止對話
                    logger.error(f"RAG retrieval error during chat stream: {e}")
                    citations = []
                    context_text = f"（檢索過程發生異常: {type(e).__name__}）"

            citation_data = [c.model_dump() for c in citations]
            yield f"event: citations\ndata: {json.dumps(citation_data, ensure_ascii=False)}\n\n"
            # 記憶區收據：讓介面能顯示「這次回答參考了 N 筆記憶」；不含記憶內容本身。
            yield f"event: memory\ndata: {json.dumps(memory_receipt, ensure_ascii=False)}\n\n"

            prompt_parts = [base_sys_prompt]
            if memory_text:
                prompt_parts.append(memory_text)
            if req.enable_rag and citations:
                prompt_parts.append(context_text)
            full_system_prompt = "\n\n".join(prompt_parts)

            async for token in llm_client.stream_chat(
                messages=llm_msgs,
                system_prompt=full_system_prompt,
                provider=chat_provider,
                model=chat_model,
            ):
                data_payload = json.dumps({"token": token}, ensure_ascii=False)
                yield f"event: message\ndata: {data_payload}\n\n"
        except asyncio.CancelledError:
            raise  # 使用者關閉分頁／取消請求：不需要再送任何事件
        except Exception as e:  # noqa: BLE001 — 例外一律轉成可讀訊息，不留下卡住的 UI
            logger.error(f"Chat stream failed: {e}", exc_info=True)
            payload = json.dumps(
                {"token": f"\n\n[對話串流中止：{type(e).__name__}；詳見本機服務日誌]"},
                ensure_ascii=False,
            )
            yield f"event: message\ndata: {payload}\n\n"
        finally:
            # 這一行是介面能離開「回覆中」的唯一保證。
            yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/chat/sessions")
def get_chat_sessions():
    db = get_db()
    with db.session_scope() as session:
        rows = session.query(RAGChatSession).order_by(RAGChatSession.updated_at.desc()).all()
        results = []
        for r in rows:
            title = (r.title or "").strip()
            if not title or title == "新對話":
                first_msg = session.query(RAGChatMessage).filter_by(session_id=r.id, role="user").order_by(RAGChatMessage.created_at.asc()).first()
                if first_msg and first_msg.content:
                    title = first_msg.content.strip().split("\n")[0][:28]
                    r.title = title
            msg_count = session.query(RAGChatMessage).filter_by(session_id=r.id).count()
            results.append({
                "id": r.id,
                "title": title or "未命名對話",
                "message_count": msg_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None
            })
        return results


@router.post("/chat/sessions")
def create_or_update_session(req: Optional[CreateSessionRequest] = None):
    req = req or CreateSessionRequest()
    s_id = req.session_id or str(uuid.uuid4())
    s_title = (req.title or "新對話").strip()
    db = get_db()
    with db.session_scope() as session:
        existing = session.query(RAGChatSession).filter_by(id=s_id).first()
        now = get_local_now()
        if existing:
            if s_title and s_title != "新對話":
                existing.title = s_title
            existing.updated_at = now
        else:
            new_s = RAGChatSession(id=s_id, title=s_title or "新對話", created_at=now, updated_at=now)
            session.add(new_s)

    return {"session_id": s_id, "title": s_title}


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(session_id: str):
    db = get_db()
    with db.session_scope() as session:
        session.query(RAGChatMessage).filter_by(session_id=session_id).delete()
        session.query(RAGChatSession).filter_by(id=session_id).delete()
    return {"success": True, "message": "對話紀錄已成功刪除"}


@router.get("/chat/messages/{session_id}")
def get_chat_messages(session_id: str):
    db = get_db()
    with db.session_scope() as session:
        rows = session.query(RAGChatMessage).filter_by(session_id=session_id).order_by(RAGChatMessage.created_at.asc()).all()
        return [
            {
                "id": r.id,
                "session_id": r.session_id,
                "role": r.role,
                "content": r.content,
                "citations": json.loads(r.citations) if r.citations else [],
                "provider": r.provider,
                "model": r.model,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in rows
        ]


@router.post("/chat/messages")
def save_chat_message(req: SaveMessageRequest):
    db = get_db()
    with db.session_scope() as session:
        msg = RAGChatMessage(
            session_id=req.session_id,
            role=req.role,
            content=req.content,
            citations=json.dumps(req.citations, ensure_ascii=False) if req.citations else None,
            provider=req.provider,
            model=req.model,
            created_at=get_local_now()
        )
        session.add(msg)
        sess = session.query(RAGChatSession).filter_by(id=req.session_id).first()
        if sess:
            sess.updated_at = get_local_now()
            if req.role == "user" and (not sess.title or sess.title == "新對話"):
                sess.title = req.content.strip().split("\n")[0][:28]
        else:
            title = req.content.strip().split("\n")[0][:28] if req.role == "user" else "新對話"
            now = get_local_now()
            new_sess = RAGChatSession(id=req.session_id, title=title, created_at=now, updated_at=now)
            session.add(new_sess)
    return {"success": True}
