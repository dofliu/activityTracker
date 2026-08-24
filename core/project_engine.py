import re
import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, ProjectState, OpenLoop, GitHubRepoState, GitHubPREvent
from core.time_utils import get_local_now

# 未歸戶的收容桶：不是真的工作項目，不應出現在進行中工作、提醒與簡報裡
BUCKET_PROJECT_KEYS = {
    "general",
    "general / notes",
    "general/notes",
    "general / unassigned",
    "unassigned",
}

OPEN_LOOP_STATUSES = {"open", "stale", "resolved", "superseded"}


def is_bucket_project(project_key: str | None) -> bool:
    """判斷是否為未歸戶的收容桶"""
    return (project_key or "").strip().lower() in BUCKET_PROJECT_KEYS


def clean_open_loop_title(title: str) -> str:
    raw_title = (title or "").strip()
    cleaned = re.sub(
        r"^\s*(\*\*)?(優先級|Priority)\s*\d+.*?\1[：:\s]*",
        "",
        raw_title,
    ).strip()
    cleaned = re.sub(r"^[：:\s\-*]+", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or raw_title


def open_loop_fingerprint(project_key: str, title: str) -> str:
    feature = re.sub(
        r"[\s\*\`\(\)\[\]【】\.,:;!？。，：；_\\/\-]",
        "",
        clean_open_loop_title(title),
    ).lower()
    raw = f"{normalize_project_name(project_key).lower()}|{feature}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


# 卡片上「上次做到哪」只需要一句話，過長的 commit 內文或提問全文會把版面撐爆
ACTION_SUMMARY_MAX_LEN = 70


def summarize_action(text: str, max_len: int = ACTION_SUMMARY_MAX_LEN) -> str:
    """把任意長度的活動描述壓成單行摘要

    只取第一段有意義的內容（commit 的標題行、提問的第一句），
    去掉換行與多餘空白，超長則截斷。
    """
    if not text:
        return ""

    # 只取第一個非空行：git commit 的 body、AI 提問的後續段落都不需要
    first_line = ""
    for line in str(text).splitlines():
        if line.strip():
            first_line = line.strip()
            break

    collapsed = re.sub(r"\s+", " ", first_line)
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[:max_len].rstrip() + "…"

def shorten_filename(name: str, max_len: int = 26) -> str:
    """縮短過長檔名但保留副檔名，讓多檔摘要不會把「等共 N 個檔案」擠掉"""
    if not name or len(name) <= max_len:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) <= 5:
        keep = max(max_len - len(ext) - 2, 6)
        return f"{stem[:keep]}….{ext}"
    return name[:max_len] + "…"


# 快取機制：避免前端每 4 秒輪詢時重複全量查詢三張表
_PROJECT_CACHE: List[Dict[str, Any]] = []
_LAST_PROJECT_REFRESH_TIME: float = 0.0
_PROJECT_CACHE_TTL: float = 30.0  # 30 秒快取


CATEGORY_FOLDERS = {
    'planning_writing', 'published', 'submitted', 'drafts', 'archive', 'archives',
    '2026', '2025', '2024', 'papers', 'projects', 'paper&patent', '01.國際期刊(發表年月)',
    '01_最新論文', 'dropbox', 'project_academic', 'project_codingsimulation',
    'personalhelper', 'users', 'user', 'documents', 'desktop', 'downloads'
}

SUBFOLDER_BLACKLIST = {
    '14_roles', '01_knowledge_base', '02_daily_reports', '03_roadmap', '05_experiments',
    '06_datasets', '07_source_code', '08_models', '10_paper_draft', '11_todo',
    'draft_paper', 'daily_report', 'papers', 'research_ideas', 'core', 'synthesizer',
    'watchers', 'integrations', 'notifiers', 'scripts', 'web', 'logs', 'checkpoints',
    'tests', 'docs', 'static', 'code', 'experiments', 'knowledgebase', 'prompts',
    'submitted', 'draft', 'drafts', 'archive', 'archives', 'outputs', 'results',
    'bladedamage', 'blade_damage', 'general / notes', 'general / unassigned',
    'pytest-cache-files-skngqfx2', 'source', 'manuscripts', 'render_manuscript',
    'render_manuscript_final', 'response', 'response_final', 'response_final2',
    'response_final3', 'closure_qa', 'word_pdf_v7', 'word_pdf', 'clean', 'diff',
    'figures', 'tables', 'data', 'temp', 'tmp'
}


def resolve_project_from_path(file_path_str: str | None) -> str:
    """從檔案路徑階層嚴謹解析出真實所屬的專案/論文根目錄名稱 (Top-Down Canonical Resolver)"""
    if not file_path_str:
        return "General / Unassigned"

    p = Path(file_path_str).resolve()

    # 1. 優先檢查 Git 根目錄 (若在 Git repo 內，以 Git repo 根目錄為專案)
    curr = p.parent if p.is_file() or '.' in p.name else p
    while curr != curr.parent:
        if (curr / '.git').exists():
            return curr.name
        curr = curr.parent

    # 2. 依序 Top-Down 尋找最上層有意義的專案/論文根目錄
    parts = p.parts
    dir_parts = parts[:-1] if ('.' in parts[-1] or p.is_file()) else parts

    for idx, part in enumerate(dir_parts):
        clean = part.lower().replace('\\', '').replace('/', '').strip()
        # 跳過磁碟機代號與分類路徑 (如 Dropbox, Planning_Writing, Submitted)
        if clean in CATEGORY_FOLDERS or clean in ['d:', 'c:', '']:
            continue

        # 跳過通用過渡或版本子目錄 (如 _cowork_v11, 00_final_submission, response_final)
        if (clean in SUBFOLDER_BLACKLIST or 
            clean.startswith('_cowork') or 
            clean.startswith('00_final') or 
            clean.startswith('01_') or 
            clean.startswith('02_') or
            clean.startswith('1st') or
            clean.startswith('2nd') or
            clean.startswith('response') or
            clean.startswith('closure')):
            continue

        # 命中第一層實質專案根目錄！
        return part

    return "General / Notes"


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
                project_stats[repo_norm]["last_action"] = f"Git: {summarize_action(g.message)} (+{g.insertions}/-{g.deletions})"

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
                    files_preview = ", ".join(shorten_filename(n) for n in distinct_recent[:2])
                    more = f" 等共 {len(distinct_recent)} 個檔案" if len(distinct_recent) > 2 else " 共 2 個檔案"
                    project_stats[p_name]["last_action"] = f"異動 {files_preview}{more}"
                else:
                    detail = latest_f.diff_summary or ""
                    suffix = f" ({detail})" if detail else ""
                    project_stats[p_name]["last_action"] = f"[{latest_f.action.upper()}] {shorten_filename(latest_f.file_name, 34)}{suffix}"

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
                project_stats[norm_tag]["last_action"] = f"[{a.platform.upper()}] {summarize_action(a.prompt_text)}"

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
            # 收容桶不是工作項目，不列入進行中清單
            if is_bucket_project(p.project_key):
                continue

            idle_days = (now - p.last_activity_at).days
            
            loops_count = session.query(OpenLoop).filter(
                OpenLoop.project_key == p.project_key,
                OpenLoop.status == "open",
            ).count()

            # 查詢對應的 GitHub 遠端倉庫與 PR 狀態
            gh_repo = session.query(GitHubRepoState).filter(
                (GitHubRepoState.repo_name == p.display_name) |
                (GitHubRepoState.repo_name == p.project_key)
            ).first()

            github_info = None
            github_url = None
            if gh_repo:
                github_url = gh_repo.html_url
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

            # 4. 解析本機真實目錄 (Local Path)
            local_path = None
            # 優先從近期檔案異動回溯真實專案目錄
            latest_f = session.query(FileActivityEvent).filter(
                (FileActivityEvent.project_name == p.project_key) |
                (FileActivityEvent.project_name == p.display_name)
            ).order_by(desc(FileActivityEvent.timestamp)).first()
            if latest_f and latest_f.file_path:
                fp = Path(latest_f.file_path).resolve()
                curr = fp.parent if fp.is_file() else fp
                while curr != curr.parent:
                    if curr.name.lower() == p.project_key.lower():
                        local_path = str(curr)
                        break
                    curr = curr.parent
                if not local_path and fp.parent.exists():
                    local_path = str(fp.parent)

            # 次之從 AI 活動的 cwd 判定
            latest_ai = session.query(AIPromptEvent).filter(
                AIPromptEvent.turn_key.isnot(None),
                (AIPromptEvent.project_tag == p.project_key) |
                (AIPromptEvent.project_tag == p.display_name) |
                (AIPromptEvent.cwd.like(f"%{p.project_key}%"))
            ).order_by(desc(AIPromptEvent.timestamp)).first()

            if not local_path and latest_ai and latest_ai.cwd:
                if os.path.exists(latest_ai.cwd):
                    local_path = str(Path(latest_ai.cwd).resolve())

            # 備援從常用根目錄探測
            if not local_path:
                candidates = [
                    Path("D:/Project_CodingSimulation") / p.project_key,
                    Path("D:/Project_CodingSimulation/PersonalHelper") / p.project_key,
                    Path("D:/Project_CodingSimulation/researchTopic") / p.project_key,
                    Path("D:/Project_CodingSimulation/courseRelated") / p.project_key,
                    Path("D:/Dropbox/Project_Academic/Paper&Patent/01.國際期刊(發表年月)/Planning_Writing") / p.project_key,
                    Path("D:/Dropbox/Project_Academic/Paper&Patent/01.國際期刊(發表年月)/Submitted") / p.project_key,
                ]
                for cand in candidates:
                    if cand.exists():
                        local_path = str(cand.resolve())
                        break

            if not local_path and p.project_key == "Agent Development":
                local_path = str(Path("D:/Project_CodingSimulation/PersonalHelper/activityTracker").resolve())

            # 5. 提取最新 AI 對話資訊
            ai_info = None
            if latest_ai:
                ai_info = {
                    "platform": latest_ai.platform,
                    "url": latest_ai.url,
                    "prompt": latest_ai.prompt_text[:120] if latest_ai.prompt_text else None,
                    "response": latest_ai.response_text[:160]
                    if latest_ai.response_status == "final_candidate" and latest_ai.response_text
                    else None,
                    "response_status": latest_ai.response_status or "legacy_unverified",
                    "source_path": latest_ai.source_path,
                    "source_position": latest_ai.source_position,
                    "conv_id": latest_ai.conversation_id,
                    "cwd": latest_ai.cwd
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
                "local_path": local_path,
                "github_url": github_url,
                "github": github_info,
                "ai_info": ai_info
            })

        _PROJECT_CACHE = result
        return result


def get_open_loops_list(
    project_key: Optional[str] = None,
    statuses: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """預設只回傳 actionable open items；stale 必須明確要求。"""
    db = get_db()
    requested_statuses = statuses or {"open"}
    invalid = requested_statuses - OPEN_LOOP_STATUSES
    if invalid:
        raise ValueError(f"Invalid Open Loop status: {sorted(invalid)}")
    with db.session_scope() as session:
        query = session.query(OpenLoop).filter(OpenLoop.status.in_(requested_statuses))
        if project_key:
            query = query.filter(func.lower(OpenLoop.project_key) == project_key.lower())
        loops = query.order_by(desc(OpenLoop.created_at)).all()
        return [
            {
                "id": l.id,
                "project_key": l.project_key,
                "title": l.title,
                "source_type": l.source_type,
                "status": l.status,
                "confidence": l.confidence,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M"),
                "last_seen_at": l.last_seen_at.strftime("%Y-%m-%d %H:%M") if l.last_seen_at else None,
                "resolution_note": l.resolution_note,
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
    """新增或更新未結事項 (Open Loop)，自動去除前綴標籤並具備特徵碼模糊去重機制"""
    db = get_db()
    clean_title = clean_open_loop_title(title)

    norm_key = normalize_project_name(project_key) if project_key else "General"
    if norm_key in ["General / Unassigned", ""]:
        norm_key = "General"

    fingerprint = open_loop_fingerprint(norm_key, clean_title)
    now = get_local_now()

    with db.session_scope() as session:
        existing = session.query(OpenLoop).filter(
            func.lower(OpenLoop.project_key) == norm_key.lower(),
            OpenLoop.fingerprint == fingerprint,
        ).order_by(desc(OpenLoop.created_at)).first()

        if not existing:
            legacy_items = session.query(OpenLoop).filter(
                func.lower(OpenLoop.project_key) == norm_key.lower(),
                OpenLoop.fingerprint.is_(None),
            ).all()
            for candidate in legacy_items:
                candidate.fingerprint = open_loop_fingerprint(
                    candidate.project_key,
                    candidate.title,
                )
                if candidate.fingerprint == fingerprint:
                    existing = candidate
                    break

        if existing:
            existing.title = clean_title
            existing.last_seen_at = now
            existing.updated_at = now
            if existing.status in {"open", "stale"}:
                existing.status = "open"
                existing.resolved_at = None
                existing.resolution_note = None
            return existing.id

        loop = OpenLoop(
            project_key=norm_key,
            title=clean_title,
            source_type=source_type,
            source_event_id=source_event_id,
            confidence=confidence,
            created_at=now,
            status="open",
            fingerprint=fingerprint,
            last_seen_at=now,
            updated_at=now,
        )
        session.add(loop)
        session.flush()
        return loop.id


def reconcile_open_loop_lifecycle() -> Dict[str, int]:
    """回填 legacy fingerprint，並將重複 actionable item 標為 superseded。"""
    db = get_db()
    now = get_local_now()
    backfilled = 0
    superseded = 0
    seen: Dict[str, int] = {}

    with db.session_scope() as session:
        loops = session.query(OpenLoop).order_by(OpenLoop.created_at, OpenLoop.id).all()
        for loop in loops:
            fingerprint = loop.fingerprint or open_loop_fingerprint(loop.project_key, loop.title)
            if not loop.fingerprint:
                loop.fingerprint = fingerprint
                backfilled += 1
            if loop.status not in {"open", "stale"}:
                continue
            if fingerprint not in seen:
                seen[fingerprint] = loop.id
                continue

            canonical_id = seen[fingerprint]
            loop.status = "superseded"
            loop.resolved_at = now
            loop.updated_at = now
            loop.resolution_note = f"Duplicate of Open Loop #{canonical_id}"
            superseded += 1

    return {"backfilled": backfilled, "superseded": superseded}


def transition_open_loop(loop_id: int, new_status: str, note: str | None = None) -> Dict[str, Any]:
    """執行可稽核 lifecycle transition；reopen 使用 `open`。"""
    normalized = (new_status or "").strip().lower()
    if normalized not in OPEN_LOOP_STATUSES:
        raise ValueError(f"Invalid Open Loop status: {new_status}")

    db = get_db()
    now = get_local_now()
    with db.session_scope() as session:
        loop = session.query(OpenLoop).filter_by(id=loop_id).first()
        if not loop:
            raise LookupError(f"Open Loop {loop_id} not found")
        old_status = loop.status or ("resolved" if loop.resolved_at else "open")
        loop.status = normalized
        loop.updated_at = now
        loop.resolution_note = (note or "").strip() or None
        if normalized in {"resolved", "superseded"}:
            loop.resolved_at = now
        else:
            loop.resolved_at = None
        if normalized == "open":
            loop.last_seen_at = now
        return {
            "id": loop.id,
            "old_status": old_status,
            "status": normalized,
            "resolution_note": loop.resolution_note,
        }


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

                # 1. 優先從括號或反引號中抽出專案標籤，如 (`activityTracker`) 或 (`113-01 離岸風電實務`) 或 【wavePowerSimuPLC】
                all_brackets = re.findall(r"[`(（【\[]([^`()（）【\]]+?)[`)）】\]]", item_text)
                for cand in all_brackets:
                    cand_clean = cand.strip("`'\" ").strip()
                    if cand_clean and not any(k in cand_clean for k in ["優先級", "Priority", "待辦", "今日", "明日", "高", "中", "低"]):
                        norm_cand = normalize_project_name(cand_clean)
                        if norm_cand not in ["General / Unassigned", "General", "General / Notes"]:
                            detected_project = norm_cand
                            break

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
