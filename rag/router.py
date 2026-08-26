import os
import sys
import uuid
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.database import get_db
from core.models import RAGIndexedFolder, RAGIndexedFile, RAGChatSession, RAGChatMessage
from core.platform_services import open_local_path
from core.time_utils import get_local_now
from rag.config import rag_settings
from rag.scanner import scanner, progress
from rag.vector_store import vector_store
from rag.retriever import bm25_service
from rag.retrieval.registry import retriever_registry
from rag.llm_gateway import llm_gateway
from rag.parsers.parser_hub import parser_hub

logger = logging.getLogger("OmniContext.RAG.Router")
router = APIRouter(prefix="/api/v1/rag", tags=["DeskRAG Knowledge & Chat"])


# Request Models
class AddFolderRequest(BaseModel):
    path: str
    name: Optional[str] = None


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
def add_folder(req: AddFolderRequest, background_tasks: BackgroundTasks):
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

    background_tasks.add_task(scanner.run_indexing_task, folder_id)
    return {
        "success": True,
        "folder_id": folder_id,
        "path": folder_path,
        "message": "目錄已成功加入，正在背景建立索引..."
    }


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int):
    db = get_db()
    with db.session_scope() as session:
        fld = session.query(RAGIndexedFolder).filter_by(id=folder_id).first()
        if not fld:
            raise HTTPException(status_code=404, detail="找不到指定的資料夾")

        f_path = fld.path
        files = session.query(RAGIndexedFile).filter_by(folder_id=folder_id).all()
        for f in files:
            vector_store.delete_by_file_path(f.path)
            bm25_service.delete_by_file_path(f.path)

        session.query(RAGIndexedFile).filter_by(folder_id=folder_id).delete()
        session.query(RAGIndexedFolder).filter_by(id=folder_id).delete()

    return {"success": True, "message": f"資料夾 {f_path} 及其索引已成功刪除。"}


@router.post("/scan")
def trigger_scan(background_tasks: BackgroundTasks, folder_id: Optional[int] = None):
    if progress.is_running:
        return {"success": False, "message": "索引掃描任務正在執行中..."}
    background_tasks.add_task(scanner.run_indexing_task, folder_id)
    return {"success": True, "message": "已觸發知識庫掃描與索引任務。"}


@router.get("/progress")
def get_progress():
    data = progress.to_dict()
    data["total_indexed_chunks"] = vector_store.count()
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
    return {
        "default": retriever_registry.default_strategy,
        "strategies": retriever_registry.list_strategies()
    }


@router.post("/chat")
async def chat_stream(req: ChatRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="對話訊息不可為空")

    last_user_message = req.messages[-1].content
    citations = []
    context_text = ""

    if req.enable_rag:
        citations = retriever_registry.retrieve(
            query=last_user_message,
            strategy=req.retrieval_strategy,
            top_k=req.top_k,
            alpha=req.hybrid_alpha,
            score_threshold=req.score_threshold or 0.0
        )
        context_text = retriever_registry.format_context_prompt(citations)

    base_sys_prompt = req.custom_system_prompt or rag_settings.DEFAULT_SYSTEM_PROMPT

    if req.enable_rag and citations:
        full_system_prompt = f"{base_sys_prompt}\n\n{context_text}"
    else:
        full_system_prompt = base_sys_prompt

    llm_msgs = [{"role": m.role, "content": m.content} for m in req.messages]

    async def event_generator():
        citation_data = [c.model_dump() for c in citations]
        yield f"event: citations\ndata: {json.dumps(citation_data, ensure_ascii=False)}\n\n"

        async for token in llm_gateway.stream_chat(
            messages=llm_msgs,
            system_prompt=full_system_prompt,
            provider=req.provider,
            model=req.model
        ):
            data_payload = json.dumps({"token": token}, ensure_ascii=False)
            yield f"event: message\ndata: {data_payload}\n\n"

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
        return [
            {
                "id": r.id,
                "title": r.title,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None
            }
            for r in rows
        ]


@router.post("/chat/sessions")
def create_or_update_session(session_id: Optional[str] = None, title: Optional[str] = "新對話"):
    s_id = session_id or str(uuid.uuid4())
    s_title = title or "新對話"
    db = get_db()
    with db.session_scope() as session:
        existing = session.query(RAGChatSession).filter_by(id=s_id).first()
        now = get_local_now()
        if existing:
            existing.title = s_title
            existing.updated_at = now
        else:
            new_s = RAGChatSession(id=s_id, title=s_title, created_at=now, updated_at=now)
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
        # Touch session updated_at
        sess = session.query(RAGChatSession).filter_by(id=req.session_id).first()
        if sess:
            sess.updated_at = get_local_now()
    return {"success": True}
