import os
import yaml
from pathlib import Path
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
import json

from .database import get_db
from .config import get_config, DEFAULT_CONFIG_PATH
from .models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary, ProjectState, OpenLoop, GitHubRepoState, GitHubPREvent
from .manager import get_manager
from .time_utils import get_local_now
from .project_engine import get_active_projects_list, get_open_loops_list, refresh_project_states
from synthesizer.aggregator import (
    generate_daily_summary_pipeline,
    generate_periodic_checkpoint,
    list_periodic_checkpoints
)

app = FastAPI(
    title="OmniContext Local Engine & Web Dashboard",
    description="個人全景上下文與活動記憶核心 API 與 Web 儀表板",
    version="1.2.0"
)

# 啟用 CORS 允許 Chrome Extension 和本機前端存取
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 掛載 Web 靜態資源目錄
WEB_DIR = Path(__file__).parent.parent / "web"
if not WEB_DIR.exists():
    WEB_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h2>OmniContext Web Dashboard is initializing... Please refresh shortly.</h2>")


# =====================================================================
# Pydantic 請求與回應結構模型
# =====================================================================
class AIPromptCreate(BaseModel):
    platform: str = Field(..., description="gemini, chatgpt, claude, manus, claude_code, codex, antigravity")
    url: Optional[str] = None
    conversation_id: Optional[str] = None
    prompt_text: str = Field(..., description="使用者輸入的 Prompt")
    response_text: Optional[str] = Field(None, description="AI 回應文本摘要")
    project_tag: Optional[str] = None
    cwd: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class FileActivityCreate(BaseModel):
    file_path: str
    file_name: str
    file_type: str
    action: str
    size_bytes: int = 0
    diff_summary: Optional[str] = None
    project_name: Optional[str] = None


class GitActivityCreate(BaseModel):
    repo_name: str
    repo_path: str
    commit_hash: str
    branch: str = "main"
    author: Optional[str] = None
    message: str
    files_changed_count: int = 0
    insertions: int = 0
    deletions: int = 0


class WindowEventCreate(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float
    app_name: str
    window_title: str
    category: str = "Uncategorized"


class GenerateSummaryRequest(BaseModel):
    target_date: Optional[str] = Field(None, description="格式 YYYY-MM-DD，若無則為今天")
    start_date: Optional[str] = Field(None, description="自訂區間起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="自訂區間結束日期 YYYY-MM-DD")
    provider: Optional[str] = Field(None, description="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    force_refresh: bool = Field(False, description="是否覆蓋已存在的摘要")


class BrowseFolderRequest(BaseModel):
    initial_dir: Optional[str] = None


class GenerateCheckpointRequest(BaseModel):
    hours: int = Field(2, ge=1, le=24, description="回溯時數")



# =====================================================================
# 1. 監控生命週期與控制 API
# =====================================================================
@app.get("/api/v1/health")
def health_check():
    return {"status": "ok", "service": "OmniContext", "time": get_local_now().isoformat()}


@app.get("/api/v1/control/status")
def get_control_status():
    manager = get_manager()
    return manager.get_status()


@app.post("/api/v1/control/start")
def start_monitoring():
    manager = get_manager()
    return manager.start_all()


@app.post("/api/v1/control/stop")
def stop_monitoring():
    manager = get_manager()
    return manager.stop_all()


class OpenPathRequest(BaseModel):
    path: Optional[str] = None
    action: Optional[str] = "explorer"  # "explorer" | "vscode" | "terminal" | "browser"
    url: Optional[str] = None


@app.post("/api/v1/control/open_path")
def open_system_path(payload: OpenPathRequest):
    """在宿主機直接開啟本機資料夾、VS Code、終端機或指定網頁"""
    import subprocess
    import os
    import sys
    import webbrowser

    try:
        if payload.url:
            webbrowser.open(payload.url)
            return {"status": "success", "message": f"已在瀏覽器開啟: {payload.url}"}

        target_path = os.path.abspath(payload.path) if payload.path else None
        if not target_path or not os.path.exists(target_path):
            return {"status": "error", "message": f"本機路徑不存在: {payload.path}"}

        if payload.action == "vscode":
            subprocess.Popen(f'code "{target_path}"', shell=True)
            return {"status": "success", "message": f"已在 VS Code 開啟: {target_path}"}

        elif payload.action == "terminal":
            if sys.platform == "win32":
                folder = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
                subprocess.Popen(f'start powershell.exe -NoExit -Command "Set-Location \'{folder}\'"', shell=True)
            return {"status": "success", "message": f"已在終端機開啟: {target_path}"}

        else: # explorer
            if sys.platform == "win32":
                norm_path = os.path.normpath(target_path)
                import ctypes
                # 使用 ShellExecuteW 直接向 Windows Desktop Shell 發送開啟視窗指令
                ret = ctypes.windll.shell32.ShellExecuteW(None, "open", norm_path, None, None, 1)
                if ret <= 32:
                    # 備援：使用 cmd /c start 開啟前台視窗
                    subprocess.Popen(f'cmd.exe /c start "" "{norm_path}"', shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target_path])
            else:
                subprocess.Popen(["xdg-open", target_path])

            return {"status": "success", "message": f"已在檔案總管開啟: {target_path}"}
    except Exception as e:
        logger.error(f"Error opening path {payload.path}: {e}")
        return {"status": "error", "message": str(e)}


# =====================================================================
# 2. 進行中工作 (Active Projects) & Open Loops API (P1 核心)
# =====================================================================
@app.get("/api/v1/projects/active")
def get_active_projects():
    """取得當前所有進行中專案的狀態、閒置天數與最後動作"""
    return get_active_projects_list()


@app.get("/api/v1/open-loops")
def get_open_loops(project: Optional[str] = None):
    """取得未結事項清單"""
    return get_open_loops_list(project_key=project)


@app.post("/api/v1/open-loops/{loop_id}/resolve")
def resolve_open_loop(loop_id: int):
    """將未結事項標記為已解決"""
    db = get_db()
    with db.session_scope() as session:
        loop = session.query(OpenLoop).filter_by(id=loop_id).first()
        if not loop:
            raise HTTPException(status_code=404, detail="Open loop not found")
        loop.resolved_at = get_local_now()
    return {"status": "success", "message": "Open loop marked as resolved"}


# =====================================================================
# 3. 配置動態讀寫 API
# =====================================================================
@app.get("/api/v1/config")
def get_system_config():
    cfg = get_config()
    return cfg.data


@app.post("/api/v1/config")
def update_system_config(new_config: Dict[str, Any] = Body(...)):
    try:
        with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(new_config, f, allow_unicode=True, sort_keys=False)
        
        manager = get_manager()
        manager.reload_config()
        return {"status": "success", "message": "配置更新成功並已套用至監控引擎"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


class OpenLoopCreate(BaseModel):
    project_key: str
    title: str
    source_type: Optional[str] = "manual"


@app.post("/api/v1/open-loops", status_code=201)
def add_open_loop(payload: OpenLoopCreate):
    from .project_engine import create_open_loop
    loop_id = create_open_loop(
        project_key=payload.project_key,
        title=payload.title,
        source_type=payload.source_type or "manual"
    )
    return {"status": "success", "id": loop_id, "message": "Open loop created"}


# =====================================================================
# 4. 數據採集 Ingestion API (支援 Upsert 修復 D5 與 D6 假開關過濾)
# =====================================================================
@app.post("/api/v1/events/ai", status_code=201)
def create_or_update_ai_event(payload: AIPromptCreate):
    cfg = get_config()

    # D6 假開關修復：檢查該瀏覽器平台是否啟用 (支援別名比對)
    plat = (payload.platform or "").lower().strip()
    if plat in ("claude", "claude_web"):
        browser_enabled = cfg.get("watchers.browser.claude_web", cfg.get("watchers.browser.claude", True))
    elif plat in ("chatgpt", "chatgpt_web"):
        browser_enabled = cfg.get("watchers.browser.chatgpt", True)
    elif plat in ("gemini", "gemini_web"):
        browser_enabled = cfg.get("watchers.browser.gemini", True)
    elif plat in ("manus", "manus_web"):
        browser_enabled = cfg.get("watchers.browser.manus", True)
    else:
        browser_enabled = cfg.get(f"watchers.browser.{plat}", True)

    if not browser_enabled:
        return {"status": "skipped", "message": f"{payload.platform} monitoring is disabled in settings"}

    db = get_db()
    clean_prompt = payload.prompt_text.strip()

    with db.session_scope() as session:
        # 尋找最近 10 分鐘內、相同平台與 Prompt 的記錄 (Upsert 邏輯)
        recent_cutoff = get_local_now() - timedelta(minutes=10)
        existing = (
            session.query(AIPromptEvent)
            .filter(
                AIPromptEvent.platform == payload.platform,
                AIPromptEvent.prompt_text == clean_prompt,
                AIPromptEvent.timestamp >= recent_cutoff
            )
            .order_by(AIPromptEvent.timestamp.desc())
            .first()
        )

        if existing:
            # 若已有記錄且新 payload 帶有回應內容，則更新回應
            if payload.response_text:
                existing.response_text = payload.response_text
                if payload.url: existing.url = payload.url
                if payload.project_tag: existing.project_tag = payload.project_tag
                if payload.cwd: existing.cwd = payload.cwd
            return {"status": "updated", "message": "Existing AI event updated with response"}

        # 否則新增記錄
        event = AIPromptEvent(
            platform=payload.platform,
            url=payload.url,
            conversation_id=payload.conversation_id,
            prompt_text=clean_prompt,
            response_text=payload.response_text,
            project_tag=payload.project_tag,
            cwd=payload.cwd,
            metadata_json=json.dumps(payload.metadata, ensure_ascii=False) if payload.metadata else None,
            timestamp=get_local_now()
        )
        session.add(event)
    return {"status": "created", "message": "New AI event logged"}



@app.post("/api/v1/events/file", status_code=201)
def create_file_event(payload: FileActivityCreate):
    db = get_db()
    with db.session_scope() as session:
        event = FileActivityEvent(
            file_path=payload.file_path,
            file_name=payload.file_name,
            file_type=payload.file_type,
            action=payload.action,
            size_bytes=payload.size_bytes,
            diff_summary=payload.diff_summary,
            project_name=payload.project_name,
            timestamp=get_local_now()
        )
        session.add(event)
    return {"status": "success", "message": "File event logged"}


@app.post("/api/v1/events/git", status_code=201)
def create_git_event(payload: GitActivityCreate):
    db = get_db()
    with db.session_scope() as session:
        existing = session.query(GitActivityEvent).filter_by(commit_hash=payload.commit_hash).first()
        if existing:
            return {"status": "exists", "message": "Commit already logged"}

        event = GitActivityEvent(
            repo_name=payload.repo_name,
            repo_path=payload.repo_path,
            commit_hash=payload.commit_hash,
            branch=payload.branch,
            author=payload.author,
            message=payload.message,
            files_changed_count=payload.files_changed_count,
            insertions=payload.insertions,
            deletions=payload.deletions,
            timestamp=get_local_now()
        )
        session.add(event)
    return {"status": "success", "message": "Git event logged"}


@app.post("/api/v1/events/window", status_code=201)
def create_window_event(payload: WindowEventCreate):
    db = get_db()
    with db.session_scope() as session:
        event = WindowEvent(
            start_time=payload.start_time or get_local_now(),
            end_time=payload.end_time or get_local_now(),
            duration_seconds=payload.duration_seconds,
            app_name=payload.app_name,
            window_title=payload.window_title,
            category=payload.category
        )
        session.add(event)
    return {"status": "success", "message": "Window event logged"}


# =====================================================================
# 5. 即時活動時間軸動態查詢 (Live Feed API)
# =====================================================================
@app.get("/api/v1/events/recent")
def get_recent_events(
    limit: int = Query(50, ge=1, le=200),
    event_type: str = Query("all", regex="^(all|ai|file|git|window)$"),
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
                    "response": a.response_text,
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


# =====================================================================
# 6. 週期性快照日誌 API
# =====================================================================
@app.get("/api/v1/logs/checkpoints")
def get_checkpoint_logs():
    return list_periodic_checkpoints()


@app.post("/api/v1/logs/checkpoints/generate")
def create_checkpoint_log(req: GenerateCheckpointRequest = Body(...)):
    return generate_periodic_checkpoint(hours=req.hours)


@app.get("/api/v1/logs/checkpoints/{filename}")
def read_checkpoint_file(filename: str):
    cfg = get_config()
    cp_dir = Path(cfg.get("exporters.checkpoints_dir", "logs/checkpoints"))
    if not cp_dir.is_absolute():
        cp_dir = Path(__file__).parent.parent / cp_dir

    file_path = cp_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Checkpoint file not found")
    
    return {
        "file_name": filename,
        "content": file_path.read_text(encoding="utf-8", errors="ignore")
    }


# =====================================================================
# 7. AI 每日摘要與報告 API
# =====================================================================
@app.get("/api/v1/summaries")
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


@app.get("/api/v1/summaries/{date_str}")
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


@app.post("/api/v1/summaries/generate")
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


# =====================================================================
# 8. 本機檔案瀏覽與路徑選擇 API (Folder Picker API)
# =====================================================================
@app.post("/api/v1/utils/browse-folder")
def api_browse_folder(req: Optional[BrowseFolderRequest] = None):
    """彈出本機原生資料夾選擇對話框"""
    from .fs_utils import open_native_folder_picker
    init_dir = req.initial_dir if req else None
    chosen = open_native_folder_picker(initial_dir=init_dir)
    if chosen:
        return {"status": "success", "path": chosen}
    return {"status": "cancelled", "path": None}


class GitHubConnectRequest(BaseModel):
    method: str = Field("gh_cli", description="gh_cli 或 token")
    token: Optional[str] = None


# =====================================================================
# 9. GitHub 雲端專案與 PR 智慧追蹤 API (GitHub Cloud Integration)
# =====================================================================
@app.get("/api/v1/github/status")
def get_github_status():
    """取得 GitHub 連線與認證狀態"""
    from integrations.github_client import get_github_client
    client = get_github_client()
    return client.test_connection()


@app.post("/api/v1/github/connect")
def connect_github(req: GitHubConnectRequest):
    """啟用 GitHub 認證連線 (支援本機 gh CLI 自動偵測或自訂 PAT)"""
    from integrations.github_client import get_github_client
    client = get_github_client()

    token_to_use = None
    if req.method == "token" and req.token:
        token_to_use = req.token.strip()
    elif req.method == "gh_cli":
        token_to_use = client.get_token()

    if not token_to_use:
        raise HTTPException(status_code=400, detail="未提供有效之 GitHub Token 且未偵測到 gh CLI 登入憑證")

    # 驗證 Token
    test_res = client.test_connection(token_override=token_to_use)
    if not test_res.get("connected"):
        raise HTTPException(status_code=401, detail=test_res.get("message", "Token 驗證失敗"))

    # 儲存至 config.yaml
    cfg = get_config()
    cfg.data["integrations"] = cfg.data.get("integrations", {})
    cfg.data["integrations"]["github"] = cfg.data["integrations"].get("github", {})
    cfg.data["integrations"]["github"]["enabled"] = True
    if req.method == "token":
        cfg.data["integrations"]["github"]["token"] = token_to_use
    else:
        cfg.data["integrations"]["github"]["token"] = ""  # 使用 gh CLI 動態讀取

    with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.data, f, allow_unicode=True, sort_keys=False)

    # 立即執行一次同步
    sync_res = client.sync_all(max_repos=40)
    return {
        "status": "success",
        "message": f"GitHub 帳號 @{test_res.get('username')} 連線成功！",
        "auth": test_res,
        "sync": sync_res
    }


@app.post("/api/v1/github/disconnect")
def disconnect_github():
    """解除 GitHub 連線"""
    cfg = get_config()
    if "integrations" in cfg.data and "github" in cfg.data["integrations"]:
        cfg.data["integrations"]["github"]["enabled"] = False
        cfg.data["integrations"]["github"]["token"] = ""
        with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg.data, f, allow_unicode=True, sort_keys=False)
    return {"status": "success", "message": "已解除 GitHub 整合連線"}


@app.post("/api/v1/github/sync")
def trigger_github_sync():
    """手動觸發即時同步所有 Public/Private 專案與 PR 狀態"""
    from integrations.github_client import get_github_client
    client = get_github_client()
    res = client.sync_all(max_repos=50)
    # 強制重整專案快取
    refresh_project_states(force=True)
    return res


@app.get("/api/v1/github/repos")
def list_github_repos():
    """取得所有已同步的 GitHub 遠端倉庫清單"""
    db = get_db()
    with db.session_scope() as session:
        repos = session.query(GitHubRepoState).order_by(GitHubRepoState.pushed_at.desc()).all()
        return [
            {
                "id": r.id,
                "name": r.repo_name,
                "full_name": r.full_name,
                "is_private": r.is_private,
                "html_url": r.html_url,
                "description": r.description,
                "default_branch": r.default_branch,
                "open_prs_count": r.open_prs_count,
                "open_issues_count": r.open_issues_count,
                "stars": r.stars_count,
                "pushed_at": r.pushed_at.strftime("%Y-%m-%d %H:%M") if r.pushed_at else None,
                "prs_summary": json.loads(r.metadata_json) if r.metadata_json else []
            }
            for r in repos
        ]


@app.get("/api/v1/github/prs")
def list_github_prs(state: Optional[str] = None):
    """取得所有活躍 PRs (包含 Open, Merged, CI 狀態)"""
    db = get_db()
    with db.session_scope() as session:
        query = session.query(GitHubPREvent)
        if state:
            query = query.filter_by(state=state)
        prs = query.order_by(GitHubPREvent.updated_at.desc()).limit(40).all()
        return [
            {
                "id": pr.id,
                "repo": pr.repo_name,
                "number": pr.pr_number,
                "title": pr.title,
                "state": pr.state,
                "is_draft": pr.is_draft,
                "author": pr.author,
                "html_url": pr.html_url,
                "branch_head": pr.branch_head,
                "branch_base": pr.branch_base,
                "ci_status": pr.ci_status,
                "review_state": pr.review_state,
                "created_at": pr.created_at.strftime("%Y-%m-%d %H:%M") if pr.created_at else None,
                "updated_at": pr.updated_at.strftime("%Y-%m-%d %H:%M") if pr.updated_at else None,
                "merged_at": pr.merged_at.strftime("%Y-%m-%d %H:%M") if pr.merged_at else None
            }
            for pr in prs
        ]


