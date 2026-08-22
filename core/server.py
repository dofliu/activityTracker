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
from .models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary
from .manager import get_manager
from synthesizer.aggregator import (
    generate_daily_summary_pipeline,
    generate_periodic_checkpoint,
    list_periodic_checkpoints
)

app = FastAPI(
    title="OmniContext Local Engine & Web Dashboard",
    description="個人全景上下文與活動記憶核心 API 與 Web 儀表板",
    version="1.1.0"
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
    """提供 Web 儀表板首頁"""
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h2>OmniContext Web Dashboard is initializing... Please refresh shortly.</h2>")


# =====================================================================
# Pydantic 請求與回應結構模型
# =====================================================================
class AIPromptCreate(BaseModel):
    platform: str = Field(..., description="gemini, chatgpt, claude, manus, etc.")
    url: Optional[str] = None
    conversation_id: Optional[str] = None
    prompt_text: str = Field(..., description="使用者輸入的 Prompt")
    response_text: Optional[str] = Field(None, description="AI 回應文本摘要")
    project_tag: Optional[str] = None
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
    provider: Optional[str] = Field(None, description="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    force_refresh: bool = Field(False, description="是否覆蓋已存在的摘要")


class GenerateCheckpointRequest(BaseModel):
    hours: int = Field(2, ge=1, le=24, description="回溯時數")


# =====================================================================
# 1. 監控生命週期與控制 API
# =====================================================================
@app.get("/api/v1/health")
def health_check():
    return {"status": "ok", "service": "OmniContext", "time": datetime.utcnow().isoformat()}


@app.get("/api/v1/control/status")
def get_control_status():
    """取得當前監控狀態、統計指標與目標配置"""
    manager = get_manager()
    return manager.get_status()


@app.post("/api/v1/control/start")
def start_monitoring():
    """啟動全景監控服務"""
    manager = get_manager()
    return manager.start_all()


@app.post("/api/v1/control/stop")
def stop_monitoring():
    """停止全景監控服務"""
    manager = get_manager()
    return manager.stop_all()


# =====================================================================
# 2. 配置動態讀寫 API (監控目標、資料夾、模型選擇)
# =====================================================================
@app.get("/api/v1/config")
def get_system_config():
    """取得當前完整的系統設定檔內容"""
    cfg = get_config()
    return cfg.data


@app.post("/api/v1/config")
def update_system_config(new_config: Dict[str, Any] = Body(...)):
    """更新系統設定檔並熱重載監控服務"""
    try:
        with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(new_config, f, allow_unicode=True, sort_keys=False)
        
        manager = get_manager()
        manager.reload_config()
        return {"status": "success", "message": "配置更新成功並已套用至監控引擎"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


# =====================================================================
# 3. 數據採集 Ingestion API
# =====================================================================
@app.post("/api/v1/events/ai", status_code=201)
def create_ai_event(payload: AIPromptCreate):
    db = get_db()
    with db.session_scope() as session:
        event = AIPromptEvent(
            platform=payload.platform,
            url=payload.url,
            conversation_id=payload.conversation_id,
            prompt_text=payload.prompt_text,
            response_text=payload.response_text,
            project_tag=payload.project_tag,
            metadata_json=json.dumps(payload.metadata, ensure_ascii=False) if payload.metadata else None,
            timestamp=datetime.utcnow()
        )
        session.add(event)
    return {"status": "success", "message": "AI event logged"}


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
            timestamp=datetime.utcnow()
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
            timestamp=datetime.utcnow()
        )
        session.add(event)
    return {"status": "success", "message": "Git event logged"}


@app.post("/api/v1/events/window", status_code=201)
def create_window_event(payload: WindowEventCreate):
    db = get_db()
    with db.session_scope() as session:
        event = WindowEvent(
            start_time=payload.start_time or datetime.utcnow(),
            end_time=payload.end_time or datetime.utcnow(),
            duration_seconds=payload.duration_seconds,
            app_name=payload.app_name,
            window_title=payload.window_title,
            category=payload.category
        )
        session.add(event)
    return {"status": "success", "message": "Window event logged"}


# =====================================================================
# 4. 即時活動時間軸動態查詢 (Live Feed API)
# =====================================================================
@app.get("/api/v1/events/recent")
def get_recent_events(
    limit: int = Query(50, ge=1, le=200),
    event_type: str = Query("all", regex="^(all|ai|file|git|window)$")
):
    """查詢即時活動流（供 Web 儀表板 Live Feed 動態更新）"""
    db = get_db()
    events = []

    with db.session_scope() as session:
        if event_type in ["all", "ai"]:
            ai_list = session.query(AIPromptEvent).order_by(AIPromptEvent.timestamp.desc()).limit(limit).all()
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
            file_list = session.query(FileActivityEvent).order_by(FileActivityEvent.timestamp.desc()).limit(limit).all()
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
            git_list = session.query(GitActivityEvent).order_by(GitActivityEvent.timestamp.desc()).limit(limit).all()
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
            win_list = session.query(WindowEvent).order_by(WindowEvent.start_time.desc()).limit(limit).all()
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

    # 按時間從新到舊排序
    events.sort(key=lambda x: x["timestamp"], reverse=True)
    return events[:limit]


# =====================================================================
# 5. 週期性日誌快照 (Checkpoint Logs) API
# =====================================================================
@app.get("/api/v1/logs/checkpoints")
def get_checkpoint_logs():
    """列出所有週期性快照日誌"""
    return list_periodic_checkpoints()


@app.post("/api/v1/logs/checkpoints/generate")
def create_checkpoint_log(req: GenerateCheckpointRequest = Body(...)):
    """手動產出最近 N 小時的活動日誌快照"""
    return generate_periodic_checkpoint(hours=req.hours)


@app.get("/api/v1/logs/checkpoints/{filename}")
def read_checkpoint_file(filename: str):
    """讀取特定快照日誌的內容"""
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
# 6. AI 每日摘要與報告 API
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
    target_date = req.target_date or date.today().isoformat()
    result = generate_daily_summary_pipeline(
        target_date_str=target_date,
        provider_override=req.provider,
        force_refresh=req.force_refresh
    )
    return result
