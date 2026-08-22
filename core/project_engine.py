from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import desc

from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, ProjectState, OpenLoop
from core.time_utils import get_local_now


def normalize_project_name(path_or_tag: str | None) -> str:
    """正規化專案標籤或路徑"""
    if not path_or_tag:
        return "General / Unassigned"
    p = Path(path_or_tag)
    name = p.name or p.parent.name or path_or_tag
    return name.strip()


def refresh_project_states():
    """從各類事件動態計算並更新 project_states 資料表 (字典匯總後批量更新)"""
    db = get_db()
    now = get_local_now()
    project_map: Dict[str, Dict[str, Any]] = {}

    def _record_activity(p_key: str, display: str, cat: str, act_time: datetime, act_summary: str):
        if p_key not in project_map or act_time > project_map[p_key]["last_time"]:
            project_map[p_key] = {
                "display_name": display,
                "category": cat,
                "last_time": act_time,
                "last_action": act_summary
            }

    with db.session_scope() as session:
        # 1. 收集 Git Repos
        git_events = session.query(GitActivityEvent).order_by(desc(GitActivityEvent.timestamp)).all()
        for g in git_events:
            p_key = normalize_project_name(g.repo_name)
            _record_activity(
                p_key=p_key,
                display=g.repo_name,
                cat="Coding / Development",
                act_time=g.timestamp,
                act_summary=f"Git Commit: {g.message} (+{g.insertions}/-{g.deletions})"
            )

        # 2. 收集 Files (論文與文檔)
        file_events = session.query(FileActivityEvent).order_by(desc(FileActivityEvent.timestamp)).all()
        for f in file_events:
            p_key = normalize_project_name(f.project_name)
            cat = "Research / Paper Writing" if f.file_type in [".tex", ".pdf", ".docx"] else "Coding"
            _record_activity(
                p_key=p_key,
                display=f.project_name or p_key,
                cat=cat,
                act_time=f.timestamp,
                act_summary=f"[{f.action.upper()}] {f.file_name} ({f.diff_summary or f.file_type})"
            )

        # 3. 收集 AI Prompts
        ai_events = session.query(AIPromptEvent).order_by(desc(AIPromptEvent.timestamp)).all()
        for a in ai_events:
            tag = a.project_tag or (Path(a.cwd).name if a.cwd else "AI Interactions")
            p_key = normalize_project_name(tag)
            _record_activity(
                p_key=p_key,
                display=tag,
                cat="AI / Research",
                act_time=a.timestamp,
                act_summary=f"[{a.platform.upper()}] {a.prompt_text[:60]}..."
            )

        # 4. 寫入或更新至 project_states 表
        for p_key, data in project_map.items():
            diff_days = (now - data["last_time"]).days
            status = "active" if diff_days <= 2 else ("idle" if diff_days <= 5 else "stale")

            proj = session.query(ProjectState).filter_by(project_key=p_key).first()
            if not proj:
                proj = ProjectState(
                    project_key=p_key,
                    display_name=data["display_name"],
                    category=data["category"],
                    last_activity_at=data["last_time"],
                    last_action_summary=data["last_action"],
                    status=status,
                    updated_at=now
                )
                session.add(proj)
            else:
                proj.display_name = data["display_name"]
                proj.category = data["category"]
                proj.last_activity_at = data["last_time"]
                proj.last_action_summary = data["last_action"]
                proj.status = status
                proj.updated_at = now


def get_active_projects_list() -> List[Dict[str, Any]]:
    """取得所有進行中專案清單與狀態"""
    refresh_project_states()
    db = get_db()
    now = get_local_now()

    with db.session_scope() as session:
        projects = session.query(ProjectState).order_by(desc(ProjectState.last_activity_at)).all()
        result = []
        for p in projects:
            idle_days = (now - p.last_activity_at).days
            
            loops_count = session.query(OpenLoop).filter(
                OpenLoop.project_key == p.project_key,
                OpenLoop.resolved_at.is_(None)
            ).count()

            result.append({
                "id": p.id,
                "project_key": p.project_key,
                "display_name": p.display_name,
                "category": p.category,
                "last_activity_at": p.last_activity_at.strftime("%Y-%m-%d %H:%M"),
                "last_action_summary": p.last_action_summary,
                "status": p.status,
                "idle_days": idle_days,
                "open_loops_count": loops_count
            })
        return result


def get_open_loops_list(project_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """取得未結事項清單"""
    db = get_db()
    with db.session_scope() as session:
        query = session.query(OpenLoop).filter(OpenLoop.resolved_at.is_(None))
        if project_key:
            query = query.filter(OpenLoop.project_key == project_key)
        loops = query.order_by(desc(OpenLoop.created_at)).all()
        return [
            {
                "id": l.id,
                "project_key": l.project_key,
                "title": l.title,
                "source_type": l.source_type,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M")
            }
            for l in loops
        ]
