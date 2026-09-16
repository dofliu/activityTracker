"""活動查詢、使用時間、脈絡與摘要（TODO D4，ROADMAP §13 R1）。

usage／background-tasks／context sessions／recent events／checkpoint 快照／summaries。
全部唯讀或產生報告，不改採集資料。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import logging
from core.background_tasks import get_background_task_summary
from core.config import get_config
from core.context_memory import build_recent_work_sessions
from core.context_memory import find_related_work
from core.coverage_ledger import get_daily_coverage
from core.database import get_db
from core.manager import get_manager
from core.models import AIPromptEvent
from core.models import DailySummary
from core.models import FileActivityEvent
from core.models import GitActivityEvent
from core.models import WindowEvent
from core.runtime_paths import resolve_runtime_path
from core.schemas import GenerateCheckpointRequest, GenerateSummaryRequest, RelatedMemoryRequest, UsageMilestoneEvaluateRequest
from core.usage_analytics import evaluate_daily_milestones
from core.usage_analytics import get_usage_summary
from fastapi import APIRouter
from fastapi import Body
from fastapi import HTTPException
from fastapi import Query
from pathlib import Path
from synthesizer.aggregator import generate_periodic_checkpoint
from synthesizer.aggregator import list_periodic_checkpoints
from typing import Optional

logger = logging.getLogger("OmniContext.Server")

router = APIRouter()


@router.get("/api/v1/usage/today")
def get_today_usage(date_str: Optional[str] = Query(None, alias="date")):
    try:
        manager_status = get_manager().get_status()
        return get_usage_summary(date_str, manager_status=manager_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@router.get("/api/v1/background-tasks/today")
def get_today_background_tasks(date_str: Optional[str] = Query(None, alias="date")):
    try:
        return get_background_task_summary(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@router.get("/api/v1/usage/coverage")
def get_usage_coverage(date_str: Optional[str] = Query(None, alias="date")):
    """P2.6 coverage ledger：回傳指定日期的採集器觀測時間段摘要。"""
    try:
        return get_daily_coverage(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@router.get("/api/v1/context/sessions")
def get_context_sessions(
    project: Optional[str] = Query(None, max_length=255),
    hours: Optional[int] = Query(None, ge=1, le=2160),
    gap_minutes: Optional[int] = Query(None, ge=5, le=1440),
    limit: Optional[int] = Query(None, ge=1, le=50),
):
    return build_recent_work_sessions(
        project=project,
        hours=hours,
        gap_minutes=gap_minutes,
        limit=limit,
    )


@router.post("/api/v1/context/related")
def get_related_context(payload: RelatedMemoryRequest):
    try:
        return find_related_work(
            payload.question,
            project=payload.project,
            threshold=payload.threshold,
            top_k=payload.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Local related-context retrieval unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="local_semantic_index_unavailable",
        ) from exc


@router.post("/api/v1/usage/milestones/evaluate")
def evaluate_usage_milestones(payload: UsageMilestoneEvaluateRequest):
    try:
        manager_status = get_manager().get_status()
        return evaluate_daily_milestones(
            payload.date,
            manager_status=manager_status,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@router.get("/api/v1/events/recent")
def get_recent_events(
    limit: int = Query(50, ge=1, le=200),
    event_type: str = Query("all", pattern="^(all|ai|file|git|window)$"),
    project: Optional[str] = Query(None)
):
    db = get_db()
    events = []

    with db.session_scope() as session:
        if event_type in ["all", "ai"]:
            q_ai = session.query(AIPromptEvent)
            if project:
                q_ai = q_ai.filter(
                    (AIPromptEvent.project_tag == project) |
                    (AIPromptEvent.cwd.contains(project))
                )
            ai_list = q_ai.order_by(AIPromptEvent.timestamp.desc()).limit(limit).all()
            for a in ai_list:
                events.append({
                    "id": f"ai_{a.id}",
                    "type": "ai",
                    "timestamp": a.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "title": f"[{a.platform.upper()}] {a.prompt_text[:60]}...",
                    "detail": a.prompt_text,
                    "response": a.response_text if a.response_status == "final_candidate" else None,
                    "response_status": a.response_status or "legacy_unverified",
                    "source_path": a.source_path,
                    "source_position": a.source_position,
                    "badge": a.platform,
                    "project": a.project_tag or "AI Chat"
                })

        if event_type in ["all", "file"]:
            q_file = session.query(FileActivityEvent)
            if project:
                q_file = q_file.filter(
                    (FileActivityEvent.project_name == project) |
                    (FileActivityEvent.file_path.contains(project))
                )
            file_list = q_file.order_by(FileActivityEvent.timestamp.desc()).limit(limit).all()
            for f in file_list:
                events.append({
                    "id": f"file_{f.id}",
                    "type": "file",
                    "timestamp": f.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "title": f"[{f.action.upper()}] {f.file_name}",
                    "detail": f.file_path,
                    "response": f.diff_summary or f"檔案大小: {f.size_bytes} Bytes",
                    "badge": f.file_type,
                    "project": f.project_name or "Documents"
                })

        if event_type in ["all", "git"]:
            q_git = session.query(GitActivityEvent)
            if project:
                q_git = q_git.filter(GitActivityEvent.repo_name == project)
            git_list = q_git.order_by(GitActivityEvent.timestamp.desc()).limit(limit).all()
            for g in git_list:
                events.append({
                    "id": f"git_{g.id}",
                    "type": "git",
                    "timestamp": g.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "title": f"[{g.repo_name}@{g.branch}] {g.message}",
                    "detail": f"Commit: {g.commit_hash} by {g.author}",
                    "response": f"變更: +{g.insertions} / -{g.deletions} 行 ({g.files_changed_count} 檔案)",
                    "badge": "git",
                    "project": g.repo_name
                })

        if event_type in ["all", "window"]:
            q_win = session.query(WindowEvent)
            if project:
                q_win = q_win.filter(
                    (WindowEvent.app_name == project) |
                    (WindowEvent.window_title.contains(project))
                )
            win_list = q_win.order_by(WindowEvent.start_time.desc()).limit(limit).all()
            for w in win_list:
                events.append({
                    "id": f"win_{w.id}",
                    "type": "window",
                    "timestamp": w.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "title": f"[{w.app_name}] {w.window_title[:50]}",
                    "detail": w.window_title,
                    "response": f"停留時間: {int(w.duration_seconds)} 秒 ({w.category})",
                    "badge": w.category,
                    "project": w.app_name
                })

    events.sort(key=lambda x: x["timestamp"], reverse=True)
    return events[:limit]


@router.get("/api/v1/logs/checkpoints")
def get_checkpoint_logs():
    return list_periodic_checkpoints()


@router.post("/api/v1/logs/checkpoints/generate")
def create_checkpoint_log(req: GenerateCheckpointRequest = Body(...)):
    return generate_periodic_checkpoint(hours=req.hours)


@router.get("/api/v1/logs/checkpoints/{filename}")
def read_checkpoint_file(filename: str):
    cfg = get_config()
    cp_dir = resolve_runtime_path(
        cfg.get("exporters.checkpoints_dir", "logs/checkpoints")
    )

    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid checkpoint filename")
    cp_root = cp_dir.resolve()
    file_path = (cp_root / filename).resolve()
    if file_path.parent != cp_root:
        raise HTTPException(status_code=400, detail="Invalid checkpoint path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Checkpoint file not found")
    
    return {
        "file_name": filename,
        "content": file_path.read_text(encoding="utf-8", errors="ignore")
    }


@router.get("/api/v1/summaries")
def list_summaries(limit: int = Query(20, ge=1, le=100)):
    db = get_db()
    with db.session_scope() as session:
        summaries = (
            session.query(DailySummary)
            .order_by(DailySummary.date_str.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": s.id,
                "date_str": s.date_str,
                "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else None,
                "llm_provider": s.llm_provider,
                "model_name": s.model_name,
                "raw_markdown": s.raw_markdown
            }
            for s in summaries
        ]


@router.get("/api/v1/summaries/{date_str}")
def get_summary_by_date(date_str: str):
    db = get_db()
    with db.session_scope() as session:
        summary = session.query(DailySummary).filter_by(date_str=date_str).first()
        if not summary:
            raise HTTPException(status_code=404, detail="Summary for this date not found")
        return {
            "id": summary.id,
            "date_str": summary.date_str,
            "created_at": summary.created_at.strftime("%Y-%m-%d %H:%M:%S") if summary.created_at else None,
            "llm_provider": summary.llm_provider,
            "model_name": summary.model_name,
            "raw_markdown": summary.raw_markdown
        }


@router.post("/api/v1/summaries/generate")
def generate_summary(req: GenerateSummaryRequest):
    from synthesizer.aggregator import generate_summary_pipeline
    start_d = req.start_date or req.target_date
    end_d = req.end_date or req.target_date
    result = generate_summary_pipeline(
        start_date_str=start_d,
        end_date_str=end_d,
        provider_override=req.provider,
        force_refresh=req.force_refresh
    )
    return result
