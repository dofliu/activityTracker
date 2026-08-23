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
                    # 若為單一根目錄孤立檔案 (如 researchProgress.md)
                    if len(parts) == 1 and ('.' in first or p.is_file()):
                        return "General / Notes"

                    # 若第一層為通用目錄分類 (如 Planning_Writing) 且有下一層
                    if first.lower() in CATEGORY_FOLDERS and len(parts) > 1:
                        second = parts[1]
                        if len(parts) == 2 and '.' in second:
                            return "General / Notes"
                        if second.lower() not in SUBFOLDER_BLACKLIST:
                            return second
                    elif first.lower() not in SUBFOLDER_BLACKLIST and first.lower() not in CATEGORY_FOLDERS:
                        if len(parts) > 1 or (not '.' in first):
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

    # 若單純是副檔名結尾的單一檔名 (如 researchProgress.md)
    if re.search(r"\.(md|txt|docx|tex|py|pdf)$", clean_str, re.IGNORECASE):
        return "General / Notes"

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
    """從各類事件動態計算並更新 project_states 資料表 (採用歷史事件多數決與 Git 倉庫判定)"""
    global _LAST_PROJECT_REFRESH_TIME
    now_ts = time.time()
    
    if not force and (now_ts - _LAST_PROJECT_REFRESH_TIME) < _PROJECT_CACHE_TTL:
        return

    db = get_db()
    now = get_local_now()

    # 專案活動匯總結構
    # norm_key -> {"last_time": dt, "last_action": str, "git_cnt": int, "code_cnt": int, "paper_cnt": int, "ai_cnt": int}
    project_stats: Dict[str, Dict[str, Any]] = {}

    def _ensure_proj(norm_key: str):
        if norm_key not in project_stats:
            project_stats[norm_key] = {
                "display_name": norm_key,
                "last_time": datetime.min,
                "last_action": "無動態",
                "git_cnt": 0,
                "code_cnt": 0,
                "paper_cnt": 0,
                "ai_cnt": 0
            }

    with db.session_scope() as session:
        # 1. 收集 Git Repos
        git_events = session.query(GitActivityEvent).order_by(desc(GitActivityEvent.timestamp)).all()
        for g in git_events:
            repo_norm = normalize_project_name(g.repo_name)
            if repo_norm in ["General / Unassigned", ""]:
                continue
            _ensure_proj(repo_norm)
            project_stats[repo_norm]["git_cnt"] += 1
            if g.timestamp > project_stats[repo_norm]["last_time"]:
                project_stats[repo_norm]["last_time"] = g.timestamp
                project_stats[repo_norm]["last_action"] = f"Git Commit: {g.message} (+{g.insertions}/-{g.deletions})"

        # 2. 收集 Files (論文與文檔)
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
            _ensure_proj(p_name)
            for ev in f_list:
                ext = (ev.file_type or "").lower()
                path_lower = (ev.file_path or "").lower()
                is_academic_path = any(k in path_lower for k in ["paper", "patent", "期刊", "01.", "draft_paper", "academic"])
                if ext in [".tex", ".docx", ".pdf"] or is_academic_path:
                    project_stats[p_name]["paper_cnt"] += 1
                elif ext in [".py", ".ts", ".js", ".c", ".cpp", ".rs", ".go", ".html", ".css", ".json", ".yaml", ".yml", ".sh", ".ps1"]:
                    project_stats[p_name]["code_cnt"] += 1
                elif ext in [".md", ".txt"]:
                    if is_academic_path:
                        project_stats[p_name]["paper_cnt"] += 1
                    else:
                        project_stats[p_name]["code_cnt"] += 1

            latest_f = f_list[0]
            if latest_f.timestamp > project_stats[p_name]["last_time"]:
                project_stats[p_name]["last_time"] = latest_f.timestamp
                # 統計最近同一次工作階段 (2小時內) 的異動檔案
                recent_cutoff = latest_f.timestamp - timedelta(hours=2)
                recent_files = [ev.file_name for ev in f_list if ev.timestamp >= recent_cutoff]
                distinct_recent = list(dict.fromkeys(recent_files))
                if len(distinct_recent) > 1:
                    files_preview = ", ".join(distinct_recent[:2])
                    more = f" 等共 {len(distinct_recent)} 個檔案" if len(distinct_recent) > 2 else f" 共 2 個檔案"
                    project_stats[p_name]["last_action"] = f"異動 {files_preview}{more}"
                else:
                    project_stats[p_name]["last_action"] = f"[{latest_f.action.upper()}] {latest_f.file_name} ({latest_f.diff_summary or latest_f.file_type})"

        # 3. 收集 AI Prompts
        ai_events = session.query(AIPromptEvent).order_by(desc(AIPromptEvent.timestamp)).all()
        for a in ai_events:
            tag = a.project_tag or (resolve_project_from_path(a.cwd) if a.cwd else "AI Interactions")
            norm_tag = normalize_project_name(tag)
            if norm_tag in ["General / Unassigned", ""]:
                continue
            _ensure_proj(norm_tag)
            project_stats[norm_tag]["ai_cnt"] += 1
            if a.timestamp > project_stats[norm_tag]["last_time"]:
                project_stats[norm_tag]["last_time"] = a.timestamp
                project_stats[norm_tag]["last_action"] = f"[{a.platform.upper()}] {a.prompt_text[:60]}..."

        # 4. 查詢 GitHub Repos 與本機 Git 狀態作為決定性判準
        gh_repo_names = {r.repo_name.lower() for r in session.query(GitHubRepoState).all()}

        # 5. 多數決判定專案分類
        valid_keys = set()
        for p_key, st in project_stats.items():
            if st["last_time"] == datetime.min:
                continue
            valid_keys.add(p_key)

            # 分類多數決邏輯
            is_git_repo = (p_key.lower() in gh_repo_names) or (st["git_cnt"] > 0)
            if is_git_repo or (st["code_cnt"] > st["paper_cnt"]):
                category = "Coding / Development"
            elif st["paper_cnt"] >= st["code_cnt"] and st["paper_cnt"] > 0:
                category = "Research / Paper Writing"
            elif st["ai_cnt"] > 0:
                category = "AI / Research"
            else:
                category = "General Activity"

            diff_days = (now - st["last_time"]).days
            status = "active" if diff_days <= 2 else ("idle" if diff_days <= 5 else "stale")

            proj = session.query(ProjectState).filter_by(project_key=p_key).first()
            if not proj:
                proj = ProjectState(
                    project_key=p_key,
                    display_name=p_key,
                    category=category,
                    last_activity_at=st["last_time"],
                    last_action_summary=st["last_action"],
                    status=status,
                    updated_at=now
                )
                session.add(proj)
            else:
                proj.display_name = p_key
                proj.category = category
                proj.last_activity_at = st["last_time"]
                proj.last_action_summary = st["last_action"]
                proj.status = status
                proj.updated_at = now

        # 刪除已不在 valid_keys 中的歷史碎片專案
        session.query(ProjectState).filter(~ProjectState.project_key.in_(valid_keys)).delete(synchronize_session=False)

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
    """新增或更新未結事項 (Open Loop)，具備正規化與模糊去重機制"""
    db = get_db()
    clean_title = title.strip()
    norm_key = normalize_project_name(project_key) if project_key else "General"
    if norm_key in ["General / Unassigned", ""]:
        norm_key = "General"

    # 生成用於比對去重的精簡特徵字串 (去除優先級標籤、標點與 Markdown 符號)
    stripped_text = re.sub(r"\*\*優先級\s*\d+.*?\*\*[：:]?", "", clean_title)
    stripped_text = re.sub(r"\*\*Priority\s*\d+.*?\*\*[：:]?", "", stripped_text)
    feature_text = re.sub(r"[\s\*\`\(\)\[\]【】\.,:;!？。，：；_\\/\-]", "", stripped_text).lower()

    with db.session_scope() as session:
        # 查詢同專案中尚未解決的事項
        existing_loops = session.query(OpenLoop).filter(
            OpenLoop.project_key == norm_key,
            OpenLoop.resolved_at.is_(None)
        ).all()

        for el in existing_loops:
            # 1. 完全相同
            if el.title.strip() == clean_title:
                return el.id

            # 2. 模糊特徵比對 (前 25 字元相同或特徵重合度高)
            el_stripped = re.sub(r"\*\*優先級\s*\d+.*?\*\*[：:]?", "", el.title)
            el_stripped = re.sub(r"\*\*Priority\s*\d+.*?\*\*[：:]?", "", el_stripped)
            el_feature = re.sub(r"[\s\*\`\(\)\[\]【】\.,:;!？。，：；_\\/\-]", "", el_stripped).lower()

            if feature_text and el_feature:
                if feature_text == el_feature:
                    el.title = clean_title  # 更新為最新措辭
                    return el.id
                if len(feature_text) >= 15 and len(el_feature) >= 15:
                    if feature_text[:20] == el_feature[:20]:
                        el.title = clean_title
                        return el.id

        loop = OpenLoop(
            project_key=norm_key,
            title=clean_title,
            source_type=source_type,
            source_event_id=source_event_id,
            confidence=confidence,
            created_at=get_local_now()
        )
        session.add(loop)
        session.flush()
        return loop.id


def extract_and_save_open_loops_from_summary(summary_markdown: str, date_str: str) -> List[int]:
    """從 AI 每日摘要的【進行中工作未結清單】章節自動萃取並歸戶至各專案"""
    created_ids: List[int] = []
    if not summary_markdown:
        return created_ids

    # 獲取所有已知的專案名稱集合 (供智慧匹配)
    known_projects: Dict[str, str] = {}
    try:
        active_list = get_active_projects_list()
        for p in active_list:
            known_projects[p["display_name"].lower()] = p["display_name"]
            known_projects[p["project_key"].lower()] = p["project_key"]
    except Exception:
        pass

    lines = summary_markdown.splitlines()
    in_open_loops_section = False

    for line in lines:
        stripped = line.strip()
        if "未結清單" in stripped or "Open Loops" in stripped or "明日優先級" in stripped or "待辦事項" in stripped:
            if stripped.startswith("#"):
                in_open_loops_section = True
                continue
        elif in_open_loops_section and (stripped.startswith("# ") or (stripped.startswith("## ") and "未結" not in stripped and "優先" not in stripped)):
            in_open_loops_section = False

        if in_open_loops_section:
            # 尋找 - [ ] 或 * [ ] 或 數字條列
            match = re.match(r"^[-*]\s*\[\s*\]\s*(.+)$", stripped) or re.match(r"^\d+\.\s*(.+)$", stripped)
            if match:
                item_text = match.group(1).strip()
                detected_project = "General"

                # 1. 優先從括號中抽出專案標籤，如 (`activityTracker`) 或 (claudDataProduction) 或 【wavePowerSimuPLC】
                bracket_match = re.search(r"[`(（【\[]\s*([a-zA-Z0-9_\-\.\u4e00-\u9fa5]+)\s*[`)）】\]]", item_text)
                if bracket_match:
                    cand = bracket_match.group(1).strip()
                    if cand and not any(k in cand for k in ["優先級", "Priority", "待辦", "今日", "明日", "高", "中", "低"]):
                        cand_clean = cand.strip("`'\" ")
                        detected_project = normalize_project_name(cand_clean)

                # 2. 若括號內未抽出，比對已知專案名稱
                if detected_project == "General":
                    for low_name, real_name in known_projects.items():
                        if len(low_name) >= 3 and low_name in item_text.lower():
                            detected_project = real_name
                            break

                loop_id = create_open_loop(
                    project_key=detected_project,
                    title=item_text,
                    source_type=f"daily_summary:{date_str}",
                    confidence=0.9
                )
                created_ids.append(loop_id)

    return created_ids
