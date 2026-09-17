"""專案狀態、Context Handoff 與未結事項（TODO D4，ROADMAP §13 R1）。

projects/active、handoff、open-loops 的生命週期轉換。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import logging
from core.project_engine import get_active_projects_list
from core.project_engine import get_open_loops_list
from core.project_engine import transition_open_loop
from core.schemas import OpenLoopCreate, OpenLoopTransitionRequest
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from typing import Optional
from core.handoff_engine import build_project_handoff, format_handoff_markdown

logger = logging.getLogger("OmniContext.Server")

router = APIRouter()


@router.get("/api/v1/projects/active")
def get_active_projects():
    """取得當前所有進行中專案的狀態、閒置天數與最後動作"""
    return get_active_projects_list()


@router.get("/api/v1/projects/{project_key}/handoff")
def get_project_handoff_api(
    project_key: str,
    turns: int = Query(5, ge=1, le=20, description="納入之歷史 AI 對話回合數")
):
    """取得指定專案的 Context Handoff 結構化接續 Prompt (P3-1)"""
    try:
        data = build_project_handoff(project_key, turns_limit=turns)
        markdown_text = format_handoff_markdown(data)
        return {
            "status": "success",
            "project_key": project_key,
            "display_name": data.get("display_name") or project_key,
            "markdown": markdown_text,
            "data": data
        }
    except Exception as e:
        logger.error(f"Error building handoff for {project_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/open-loops")
def get_open_loops(project: Optional[str] = None, status: str = "open"):
    """取得未結事項清單"""
    statuses = {item.strip().lower() for item in status.split(",") if item.strip()}
    try:
        return get_open_loops_list(project_key=project, statuses=statuses)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/open-loops/{loop_id}/resolve")
def resolve_open_loop(loop_id: int):
    """將未結事項標記為已解決"""
    try:
        result = transition_open_loop(loop_id, "resolved", "Resolved from dashboard")
        return {"status": "success", "transition": result}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/v1/open-loops/{loop_id}/transition")
def update_open_loop_lifecycle(loop_id: int, payload: OpenLoopTransitionRequest):
    try:
        return {
            "status": "success",
            "transition": transition_open_loop(loop_id, payload.status, payload.note),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/open-loops", status_code=201)
def add_open_loop(payload: OpenLoopCreate):
    from .project_engine import create_open_loop
    loop_id = create_open_loop(
        project_key=payload.project_key,
        title=payload.title,
        source_type=payload.source_type or "manual"
    )
    return {"status": "success", "id": loop_id, "message": "Open loop created"}
