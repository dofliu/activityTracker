import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import desc

from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, ProjectState, OpenLoop, GitHubRepoState, GitHubPREvent
from core.time_utils import get_local_now

# 快取機制：避免前端每 4 秒輪詢時重複全量查詢三張表
_PROJECT_CACHE: List[Dict[str, Any]] = []
_LAST_PROJECT_REFRESH_TIME: float = 0.0
_PROJECT_CACHE_TTL: float = 30.0  # 30 秒快取


CATEGORY_FOLDERS = {
    'planning_writing', 'published', 'drafts', 'archive', '2026', '2025', '2024',
    'papers', 'projects', 'paper&patent', '01.國際期刊(發表年月)'
}

SUBFOLDER_BLACKLIST = {
    '14_roles', '01_knowledge_base', '02_daily_reports', '03_roadmap', '05_experiments',
    '06_datasets', '07_source_code', '08_models', '10_paper_draft', '11_todo',
    'draft_paper', 'daily_report', 'papers', 'research_ideas', 'core', 'synthesizer',
    'watchers', 'integrations', 'notifiers', 'scripts', 'web', 'logs', 'checkpoints',
    'tests', 'docs', 'static', 'code', 'experiments', 'knowledgebase', 'prompts',
    'pytest-cache-files-skngqfx2'
}


def resolve_project_from_path(file_path_str: str | None) -> str:
    """從檔案路徑階層嚴謹解析出真實所屬的專案/論文根目錄名稱"""
    if not file_path_str:
        return "General / Unassigned"

    p = Path(file_path_str).resolve()

    # 1. 優先檢查 Git 根目錄 (若在 Git repo 內，以 Git repo 根目錄為專案)
    curr = p.parent
    while curr != curr.parent:
        if (curr / '.git').exists():
            return curr.name
        curr = curr.parent

    # 2. 檢查是否在監控目錄 (watch_directories) 下
    try:
        from core.config import get_config
        cfg = get_config()
        watch_dirs = [Path(d).resolve() for d in cfg.get('watchers.file_watcher.watch_directories', [])]
        for wd in watch_dirs:
            try:
                rel = p.relative_to(wd)
                parts = rel.parts
                if len(parts) >= 1:
                    first = parts[0]
                    # 若第一層為通用目錄分類 (如 Planning_Writing) 且有下一層
                    if first.lower() in CATEGORY_FOLDERS and len(parts) > 1:
                        second = parts[1]
                        if second.lower() not in SUBFOLDER_BLACKLIST:
                            return second
                    elif first.lower() not in SUBFOLDER_BLACKLIST and first.lower() not in CATEGORY_FOLDERS:
                        # 如果不是根目錄直接孤立的單一檔案
                        if len(parts) > 1 or not p.is_file():
                            return first
            except ValueError:
                continue
    except Exception:
        pass

    # 3. 倒序找有意義的目錄
    for part in reversed(p.parts[:-1]):
        clean = part.lower().strip()
        if clean not in SUBFOLDER_BLACKLIST and clean not in CATEGORY_FOLDERS and len(clean) > 2:
            return part

    return p.parent.name if p.parent else "General / Unassigned"


def normalize_project_name(path_or_tag: str | None) -> str:
    """正規化專案標籤或路徑，過濾雜訊目錄名稱"""
    if not path_or_tag:
        return "General / Unassigned"

    clean_str = str(path_or_tag).strip()
    
    # 1. 若為檔案路徑，直接使用路徑解析器
    if "\\" in clean_str or "/" in clean_str:
        return resolve_project_from_path(clean_str)

    # 2. 過濾 Codex 一次性暫存執行路徑 (如 Documents/Codex/2026-08-22/ai-gpt...)
    if "Documents" in clean_str and "Codex" in clean_str:
        return "Codex Automations"

    # 3. 過濾純 uuid 或隨機雜湊目錄 (如 pogo-95a8487568460561acd63f07d3feaa8a4bfce999)
    if re.search(r"^[0-9a-f]{8}-[0-9a-f]{4}", clean_str, re.IGNORECASE):
        return "Agent Task Runs"
    if re.search(r"pogo-[0-9a-f]{16,}", clean_str, re.IGNORECASE):
        return "Pogo Experiments"

    # 4. 檢查是否在黑名單中
    if clean_str.lower() in SUBFOLDER_BLACKLIST:
        return "General / Unassigned"

    return clean_str.strip()


def refresh_project_states(force: bool = False):
    """從各類事件動態計算並更新 project_states 資料表 (字典匯總後批量更新)"""
    global _LAST_PROJECT_REFRESH_TIME
    now_ts = time.time()
    
    if not force and (now_ts - _LAST_PROJECT_REFRESH_TIME) < _PROJECT_CACHE_TTL:
        return

    db = get_db()
    now = get_local_now()
    project_map: Dict[str, Dict[str, Any]] = {}

    def _record_activity(p_key: str, display: str, cat: str, act_time: datetime, act_summary: str):
        norm_key = normalize_project_name(p_key)
        if norm_key in ["General / Unassigned", ""]:
            return
        if norm_key not in project_map or act_time > project_map[norm_key]["last_time"]:
            project_map[norm_key] = {
                "display_name": norm_key,
                "category": cat,
                "last_time": act_time,
                "last_action": act_summary
            }

    with db.session_scope() as session:
        # 1. 收集 Git Repos
        git_events = session.query(GitActivityEvent).order_by(desc(GitActivityEvent.timestamp)).all()
        proj_gits: Dict[str, List[GitActivityEvent]] = {}
        for g in git_events:
            repo_norm = normalize_project_name(g.repo_name)
            if repo_norm not in proj_gits:
                proj_gits[repo_norm] = []
            proj_gits[repo_norm].append(g)

        for repo_name, g_list in proj_gits.items():
            latest_g = g_list[0]
            _record_activity(
                p_key=repo_name,
                display=repo_name,
                cat="Coding / Development",
                act_time=latest_g.timestamp,
                act_summary=f"Git Commit: {latest_g.message} (+{latest_g.insertions}/-{latest_g.deletions})"
            )

        # 2. 收集 Files (論文與文檔，按專案聚合多檔案異動)
        file_events = session.query(FileActivityEvent).order_by(desc(FileActivityEvent.timestamp)).all()
        proj_files: Dict[str, List[FileActivityEvent]] = {}
        for f in file_events:
            p_name = resolve_project_from_path(f.file_path) if f.file_path else normalize_project_name(f.project_name)
            if p_name in ["General / Unassigned", ""]:
                continue
            if p_name not in proj_files:
                proj_files[p_name] = []
            proj_files[p_name].append(f)

        for p_name, f_list in proj_files.items():
            latest_f = f_list[0]
            # 統計最近同一次工作階段 (2小時內) 的異動檔案
            recent_cutoff = latest_f.timestamp - timedelta(hours=2)
            recent_files = [ev.file_name for ev in f_list if ev.timestamp >= recent_cutoff]
            distinct_recent = list(dict.fromkeys(recent_files))  # 去重保留先後順序
            
            cat = "Research / Paper Writing" if any(ev.file_type in [".tex", ".pdf", ".docx", ".md"] for ev in f_list) else "Coding"
            
            if len(distinct_recent) > 1:
                files_preview = ", ".join(distinct_recent[:2])
                more = f" 等共 {len(distinct_recent)} 個檔案" if len(distinct_recent) > 2 else f" 共 2 個檔案"
                summary = f"異動 {files_preview}{more}"
            else:
                summary = f"[{latest_f.action.upper()}] {latest_f.file_name} ({latest_f.diff_summary or latest_f.file_type})"

            _record_activity(
                p_key=p_name,
                display=p_name,
                cat=cat,
                act_time=latest_f.timestamp,
                act_summary=summary
            )

        # 3. 收集 AI Prompts
        ai_events = session.query(AIPromptEvent).order_by(desc(AIPromptEvent.timestamp)).all()
        for a in ai_events:
            tag = a.project_tag or (resolve_project_from_path(a.cwd) if a.cwd else "AI Interactions")
            _record_activity(
                p_key=tag,
                display=tag,
                cat="AI / Research",
                act_time=a.timestamp,
                act_summary=f"[{a.platform.upper()}] {a.prompt_text[:60]}..."
            )

        # 4. 清理並重新同步至 project_states 表
        # 先獲取當前有效的 project keys
        valid_keys = set(project_map.keys())
        
        # 刪除已不在 valid_keys 中的歷史碎片專案
        session.query(ProjectState).filter(~ProjectState.project_key.in_(valid_keys)).delete(synchronize_session=False)

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

    _LAST_PROJECT_REFRESH_TIME = time.time()


def get_active_projects_list(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """取得所有進行中專案清單與狀態 (具備 30 秒快取)"""
    global _PROJECT_CACHE
    now_ts = time.time()

    if not force_refresh and _PROJECT_CACHE and (now_ts - _LAST_PROJECT_REFRESH_TIME) < _PROJECT_CACHE_TTL:
        return _PROJECT_CACHE

    refresh_project_states(force=force_refresh)
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

            # 查詢對應的 GitHub 遠端倉庫與 PR 狀態
            gh_repo = session.query(GitHubRepoState).filter(
                (GitHubRepoState.repo_name == p.display_name) |
                (GitHubRepoState.repo_name == p.project_key)
            ).first()

            github_info = None
            if gh_repo:
                recent_prs = (
                    session.query(GitHubPREvent)
                    .filter_by(repo_name=gh_repo.repo_name)
                    .order_by(desc(GitHubPREvent.updated_at))
                    .limit(5)
                    .all()
                )
                github_info = {
                    "full_name": gh_repo.full_name,
                    "html_url": gh_repo.html_url,
                    "is_private": gh_repo.is_private,
                    "default_branch": gh_repo.default_branch,
                    "open_prs_count": gh_repo.open_prs_count,
                    "open_issues_count": gh_repo.open_issues_count,
                    "stars_count": gh_repo.stars_count,
                    "prs": [
                        {
                            "number": pr.pr_number,
                            "title": pr.title,
                            "state": pr.state,
                            "is_draft": pr.is_draft,
                            "author": pr.author,
                            "html_url": pr.html_url,
                            "branch": f"{pr.branch_head} -> {pr.branch_base}",
                            "ci_status": pr.ci_status,
                            "review_state": pr.review_state,
                            "merged_at": pr.merged_at.strftime("%Y-%m-%d %H:%M") if pr.merged_at else None,
                            "updated_at": pr.updated_at.strftime("%Y-%m-%d %H:%M") if pr.updated_at else None
                        }
                        for pr in recent_prs
                    ]
                }

            result.append({
                "id": p.id,
                "project_key": p.project_key,
                "display_name": p.display_name,
                "category": p.category,
                "last_activity_at": p.last_activity_at.strftime("%Y-%m-%d %H:%M"),
                "last_action_summary": p.last_action_summary,
                "status": p.status,
                "idle_days": idle_days,
                "open_loops_count": loops_count,
                "github": github_info
            })

        _PROJECT_CACHE = result
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


def create_open_loop(
    project_key: str,
    title: str,
    source_type: str = "manual",
    source_event_id: Optional[int] = None,
    confidence: float = 1.0
) -> int:
    """新增一筆未結事項 (Open Loop)"""
    db = get_db()
    with db.session_scope() as session:
        # 去重：若同專案已有相同標題且未解決的事項，不重複建立
        existing = session.query(OpenLoop).filter(
            OpenLoop.project_key == project_key,
            OpenLoop.title == title.strip(),
            OpenLoop.resolved_at.is_(None)
        ).first()
        if existing:
            return existing.id

        loop = OpenLoop(
            project_key=project_key,
            title=title.strip(),
            source_type=source_type,
            source_event_id=source_event_id,
            confidence=confidence,
            created_at=get_local_now()
        )
        session.add(loop)
        session.flush()
        return loop.id


def extract_and_save_open_loops_from_summary(summary_markdown: str, date_str: str) -> List[int]:
    """從 AI 每日摘要的【進行中工作未結清單】章節自動萃取並寫入 OpenLoop 表"""
    created_ids: List[int] = []
    if not summary_markdown:
        return created_ids

    # 尋找未結清單區塊 (尋找 ## 4. 或包含「未結清單」的章節)
    lines = summary_markdown.splitlines()
    in_open_loops_section = False

    for line in lines:
        stripped = line.strip()
        if "未結清單" in stripped or "Open Loops" in stripped or "明日優先級" in stripped:
            if stripped.startswith("#"):
                in_open_loops_section = True
                continue
        elif in_open_loops_section and stripped.startswith("# ") or (in_open_loops_section and stripped.startswith("## ") and "未結" not in stripped):
            # 遇到下一個大標題，結束
            in_open_loops_section = False

        if in_open_loops_section:
            # 尋找 - [ ] 或 * [ ] 或 數字條列
            match = re.match(r"^[-*]\s*\[\s*\]\s*(.+)$", stripped)
            if match:
                item_text = match.group(1).strip()
                # 嘗試提取專案名稱，例如: **優先級 1 (activityTracker)**: ... 或 **activityTracker**: ...
                proj_match = re.search(r"\(([^)]+)\)|【([^】]+)】|\*\*([^*:]+)\*\*", item_text)
                detected_project = "General"
                if proj_match:
                    p_candidate = proj_match.group(1) or proj_match.group(2) or proj_match.group(3)
                    if p_candidate and "優先級" not in p_candidate and "Priority" not in p_candidate:
                        detected_project = normalize_project_name(p_candidate)

                loop_id = create_open_loop(
                    project_key=detected_project,
                    title=item_text,
                    source_type=f"daily_summary:{date_str}",
                    confidence=0.9
                )
                created_ids.append(loop_id)

    return created_ids
