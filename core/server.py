import logging
import os
import hashlib
import yaml
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Literal
from fastapi import FastAPI, Depends, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import json

from .database import get_db
from . import __version__
from .config import get_config
from .models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary, ProjectState, OpenLoop, GitHubRepoState, GitHubPREvent
from .manager import get_manager
from .platform_services import open_local_path, open_web_url
from .security import (
    configured_allowed_origins,
    execution_authorized,
    extension_ingest_authorized,
    is_extension_origin,
    is_loopback_host,
    merge_redacted_config,
    origin_is_allowed,
    redact_config,
)
from .extension_monitor import build_extension_status, record_extension_heartbeat
from .extension_verification import extension_verification_registry
from .capture_coverage import build_capture_coverage
from .context_memory import build_recent_work_sessions, find_related_work
from .agent_executor import (
    ExecutionRejected,
    attach_execution_actions,
    cancel_execution,
    execute_proposal,
    list_execution_receipts,
)
from .proactive_secretary import build_action_proposals, snooze_proposal
from .scheduled_tasks import (
    create_scheduled_task,
    delete_scheduled_task,
    list_scheduled_tasks,
    run_scheduled_task_now,
    update_scheduled_task,
)
from .secretary_advisor import annotate_action_proposals
from .background_tasks import get_background_task_summary
from .coverage_ledger import get_daily_coverage
from .usage_analytics import evaluate_daily_milestones, get_usage_summary
from .time_utils import get_local_now
from .runtime_paths import resolve_runtime_path, web_assets_dir
from .secret_resolver import resolve_secret_env
from .project_engine import (
    get_project_state_count,
    get_open_loops_list,
    refresh_project_states,
    transition_open_loop,
)
from .repo_sync import LocalRepositorySync, RepositorySyncRejected
from synthesizer.aggregator import (
    generate_daily_summary_pipeline,
    generate_periodic_checkpoint,
    list_periodic_checkpoints
)
from .data_lifecycle import (
    checkpoint_sqlite_database,
    run_database_maintenance,
    get_latest_maintenance_receipt,
    configured_database_path,
)
from rag.router import router as rag_router

logger = logging.getLogger("OmniContext.Server")
_startup_cfg = get_config()
_allowed_origins = configured_allowed_origins(_startup_cfg)


def browser_conversation_key(conversation_id: str | None, url: str | None) -> str:
    if conversation_id and conversation_id.strip():
        return conversation_id.strip()
    conversation_ref = (url or "unknown").strip()
    return hashlib.sha256(
        conversation_ref.encode("utf-8", errors="replace")
    ).hexdigest()[:32]


def browser_response_status(response: str | None, capture_state: str | None) -> str:
    if not (response or "").strip():
        return "missing"
    return "final_candidate" if (capture_state or "").lower() == "stable_candidate" else "partial"

app = FastAPI(
    title="OmniContext Local Engine & Web Dashboard",
    description="個人全景上下文與活動記憶核心 API 與 Web 儀表板",
    version=__version__
)

# 掛載 DeskRAG 本地知識庫子系統路由
app.include_router(rag_router)

# 僅允許本機 dashboard origins；browser extension 走獨立 write-only token boundary。
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_local_security_boundary(request: Request, call_next):
    cfg = get_config()
    client_host = request.client.host if request.client else None
    allow_remote = bool(cfg.get("security.allow_remote_clients", False))
    if not allow_remote and not is_loopback_host(client_host):
        return JSONResponse(status_code=403, content={"detail": "Remote clients are disabled"})

    origin = request.headers.get("origin")
    allowed_origins = configured_allowed_origins(cfg)
    if origin and not origin_is_allowed(origin, allowed_origins):
        # Extension 可讀 health；只有帶 ingest token 才能寫入 AI event。
        if is_extension_origin(origin) and request.url.path == "/api/v1/health":
            return await call_next(request)
        if is_extension_origin(origin) and request.url.path in {
            "/api/v1/events/ai",
            "/api/v1/extension/heartbeat",
            "/api/v1/extension/status",
        }:
            token = request.headers.get("x-omnicontext-ingest-token")
            if extension_ingest_authorized(token, cfg):
                return await call_next(request)
        return JSONResponse(status_code=403, content={"detail": "Origin is not allowed"})

    return await call_next(request)

# 掛載 Web 靜態資源目錄
WEB_DIR = web_assets_dir()
if not WEB_DIR.is_dir():
    raise RuntimeError(f"OmniContext Web assets are missing: {WEB_DIR}")

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h2>OmniContext Web Dashboard is initializing... Please refresh shortly.</h2>")


@app.get("/extension-monitor", response_class=HTMLResponse)
def extension_monitor_page():
    monitor_file = WEB_DIR / "extension-monitor.html"
    if monitor_file.exists():
        return FileResponse(str(monitor_file))
    return HTMLResponse("<h2>OmniContext Extension Monitor is initializing...</h2>")


# =====================================================================
# Pydantic 請求與回應結構模型
# =====================================================================
class AIPromptCreate(BaseModel):
    platform: str = Field(..., description="gemini, chatgpt, claude, claude_code, codex, antigravity")
    url: Optional[str] = None
    conversation_id: Optional[str] = None
    prompt_text: str = Field(..., description="使用者輸入的 Prompt")
    response_text: Optional[str] = Field(None, description="AI 回應文本摘要")
    project_tag: Optional[str] = None
    cwd: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ExtensionContentReadyReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(..., min_length=1, max_length=20)
    seen_at: datetime


class ExtensionHeartbeatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(..., min_length=1, max_length=64)
    extension_version: str = Field(..., min_length=1, max_length=32)
    ready_platforms: list[str] = Field(default_factory=list, max_length=4)
    ready_platform_receipts: list[ExtensionContentReadyReceipt] = Field(
        default_factory=list,
        max_length=4,
    )
    last_capture_status: str = Field("none", max_length=40)
    last_capture_at: Optional[datetime] = None
    last_error_code: Optional[str] = Field(None, max_length=80)
    offline_queue_size: int = Field(0, ge=0, le=100)


class ExtensionVerificationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platforms: list[str] = Field(..., min_length=1, max_length=4)
    timeout_seconds: int = Field(600, ge=60, le=1800)


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


class UsageMilestoneEvaluateRequest(BaseModel):
    date: Optional[str] = Field(None, description="本機日期 YYYY-MM-DD；僅允許當日通知")
    dry_run: bool = Field(False, description="只回傳預覽，不發送通知或寫入 receipt")


class RelatedMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=2, max_length=4000)
    project: Optional[str] = Field(None, max_length=255)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_k: int = Field(8, ge=1, le=20)


class SystemMaintenanceRequest(BaseModel):
    max_backups: int = Field(7, ge=1, le=100)
    retention_days: int = Field(90, ge=1, le=3650)
    do_backup: bool = Field(True)
    checkpoint_mode: str = Field("TRUNCATE", pattern="^(PASSIVE|FULL|RESTART|TRUNCATE)$")



# =====================================================================
# 1. 監控生命週期與控制 API
# =====================================================================
@app.get("/api/v1/health")
def health_check():
    return {"status": "ok", "service": "OmniContext", "time": get_local_now().isoformat()}


@app.get("/api/v1/extension/status")
def get_extension_monitor_status(request: Request):
    """Dashboard 可看觀測狀態；Extension 帶 token 時另可驗證 pairing。"""
    return build_extension_status(
        request.headers.get("x-omnicontext-ingest-token")
    )


@app.post("/api/v1/extension/heartbeat", status_code=202)
def receive_extension_heartbeat(payload: ExtensionHeartbeatCreate, request: Request):
    """只接受帶正確 ingest token 的非敏感 Extension heartbeat。"""
    cfg = get_config()
    token = request.headers.get("x-omnicontext-ingest-token")
    if not extension_ingest_authorized(token, cfg):
        raise HTTPException(status_code=401, detail="Extension ingest token is invalid")
    return record_extension_heartbeat(payload.model_dump())


@app.post("/api/v1/extension/verification", status_code=201)
def start_extension_verification(payload: ExtensionVerificationStart):
    """建立 process-local baseline；不保存 token、對話內容或 verification run。"""
    try:
        return extension_verification_registry.start(
            payload.platforms,
            timeout_seconds=payload.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/extension/verification/{verification_id}")
def get_extension_verification(verification_id: str):
    try:
        return extension_verification_registry.get(verification_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Verification run not found") from exc


@app.get("/api/v1/usage/today")
def get_today_usage(date_str: Optional[str] = Query(None, alias="date")):
    try:
        manager_status = get_manager().get_status()
        return get_usage_summary(date_str, manager_status=manager_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@app.get("/api/v1/background-tasks/today")
def get_today_background_tasks(date_str: Optional[str] = Query(None, alias="date")):
    try:
        return get_background_task_summary(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@app.get("/api/v1/usage/coverage")
def get_usage_coverage(date_str: Optional[str] = Query(None, alias="date")):
    """P2.6 coverage ledger：回傳指定日期的採集器觀測時間段摘要。"""
    try:
        return get_daily_coverage(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc


@app.get("/api/v1/capture/status")
def get_capture_status():
    """分開回傳 focus、web 與 transcript coverage，避免以單一 ONLINE 誤導。"""
    return build_capture_coverage()


@app.get("/api/v1/context/sessions")
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


@app.post("/api/v1/context/related")
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


@app.get("/api/v1/secretary/proposals")
def get_secretary_proposals(
    limit: int = Query(6, ge=1, le=12),
):
    """P5-1 proposal-only derived view；不保存任何建議。

    P5-R1：可選的 LLM advisory 層只能對既有 proposal 附加唯讀註解
    （預設關閉；本機 Ollama 優先；失敗自動回退 deterministic）。
    P5-R2：executor 啟用時（預設關閉）標記白名單動作；執行仍需
    execution token 與使用者逐項批准（ADR-008）。
    """
    return attach_execution_actions(
        annotate_action_proposals(build_action_proposals(limit=limit))
    )


class SnoozeProposalRequest(BaseModel):
    proposal_type: str
    project_key: str
    subject_ref: str = ""
    days: Optional[int] = 7
    dismissed: bool = False
    note: Optional[str] = None


@app.post("/api/v1/secretary/proposals/snooze")
def snooze_secretary_proposal(payload: SnoozeProposalRequest):
    """記錄「先不要再提醒我」；只寫 proposal_snoozes，不觸碰事件資料。

    這是分流清單能變準的唯一途徑：沒有回饋，系統會一直重推已被判斷為不重要的事。
    """
    return snooze_proposal(
        proposal_type=payload.proposal_type,
        project_key=payload.project_key,
        subject_ref=payload.subject_ref,
        days=payload.days,
        dismissed=payload.dismissed,
        note=payload.note,
    )


def _require_execution_token(request: Request) -> None:
    """ADR-008 D4：executor endpoints 需獨立 execution token（fail-closed）。"""
    if not execution_authorized(
        request.headers.get("x-omnicontext-execution-token"), get_config()
    ):
        raise HTTPException(
            status_code=401,
            detail="execution token is missing or invalid",
        )


class ExecuteProposalRequest(BaseModel):
    template_id: Optional[str] = None
    confirm_code: Optional[str] = None


@app.post("/api/v1/secretary/proposals/{proposal_id}/execute")
def execute_secretary_proposal(
    proposal_id: str,
    request: Request,
    payload: Optional[ExecuteProposalRequest] = None,
):
    """ADR-008 D1：只接受 proposal_id，動作由 server 端白名單 template 決定。

    body 只認兩個欄位：``template_id`` 在 server 已註冊的動作中選擇
    （預設 primary）、``confirm_code`` 供 L2 二次確認（P5-R3）；其餘
    欄位一律忽略——任何呼叫端提供的 command / path / argv 都沒有效果。
    L2 第一次呼叫（未附 confirm code）回 428 與一次性確認碼。
    """
    _require_execution_token(request)
    try:
        result = execute_proposal(
            proposal_id,
            approved_via="web_click",
            template_id=payload.template_id if payload else None,
            confirm_code=payload.confirm_code if payload else None,
        )
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc
    if result.get("status") == "confirmation_required":
        return JSONResponse(status_code=428, content=result)
    return result


@app.get("/api/v1/secretary/executions")
def get_secretary_executions(limit: int = Query(20, ge=1, le=100)):
    """Audit receipts（非敏感摘要與 digest）；唯讀，不需 execution token。"""
    return list_execution_receipts(limit)


@app.post("/api/v1/secretary/executions/{receipt_id}/cancel")
def cancel_secretary_execution(receipt_id: int, request: Request):
    _require_execution_token(request)
    try:
        return cancel_execution(receipt_id)
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


class ScheduledTaskCreateRequest(BaseModel):
    template_id: str
    params: Dict[str, Any] = Field(default_factory=dict)
    schedule_kind: str
    run_time: str = "08:30"
    weekday: Optional[int] = None
    day_of_month: Optional[int] = None
    enabled: bool = True


class ScheduledTaskUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    schedule_kind: Optional[str] = None
    run_time: Optional[str] = None
    weekday: Optional[int] = None
    day_of_month: Optional[int] = None
    params: Optional[Dict[str, Any]] = None


@app.get("/api/v1/secretary/scheduled-tasks")
def get_secretary_scheduled_tasks():
    """P5-R5 排程任務與可排程 template 清單；唯讀，不需 execution token。"""
    return list_scheduled_tasks()


@app.post("/api/v1/secretary/scheduled-tasks")
def create_secretary_scheduled_task(payload: ScheduledTaskCreateRequest, request: Request):
    """只能排程 server 註冊的 L0 唯讀 template；params 需通過白名單驗證。"""
    _require_execution_token(request)
    try:
        return create_scheduled_task(payload.model_dump())
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@app.patch("/api/v1/secretary/scheduled-tasks/{task_id}")
def update_secretary_scheduled_task(
    task_id: int, payload: ScheduledTaskUpdateRequest, request: Request
):
    _require_execution_token(request)
    try:
        return update_scheduled_task(task_id, payload.model_dump(exclude_unset=True))
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@app.delete("/api/v1/secretary/scheduled-tasks/{task_id}")
def delete_secretary_scheduled_task(task_id: int, request: Request):
    _require_execution_token(request)
    try:
        return delete_scheduled_task(task_id)
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@app.post("/api/v1/secretary/scheduled-tasks/{task_id}/run")
def run_secretary_scheduled_task(task_id: int, request: Request):
    """立即執行一次（寫 audit receipt，approved_via=web_click）。"""
    _require_execution_token(request)
    try:
        return run_scheduled_task_now(task_id, approved_via="web_click")
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@app.post("/api/v1/usage/milestones/evaluate")
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


class SystemMaintenanceRequest(BaseModel):
    max_backups: Optional[int] = 7
    retention_days: Optional[int] = 90
    dry_run: Optional[bool] = False


@app.post("/api/v1/system/maintenance")
def trigger_system_maintenance(payload: SystemMaintenanceRequest = Body(default_factory=SystemMaintenanceRequest)):
    """手動觸發資料庫生命週期維護（Checkpoint、完整性檢查、歷史修剪、線上備份、輪替）"""
    from core.data_lifecycle import run_database_maintenance
    res = run_database_maintenance(
        max_backups=payload.max_backups or 7,
        retention_days=payload.retention_days or 90,
        dry_run=payload.dry_run or False,
    )
    return res


@app.get("/api/v1/system/maintenance/receipt")
def get_system_maintenance_receipt():
    """取得最近一次資料庫維護收據與健康資訊"""
    from core.data_lifecycle import get_latest_maintenance_receipt
    receipt = get_latest_maintenance_receipt()
    if not receipt:
        return {"has_receipt": False, "status": "no_receipt", "message": "尚未執行過資料庫維護"}
    return {"has_receipt": True, "receipt": receipt, **receipt}


@app.post("/api/v1/system/wal-checkpoint")
def trigger_wal_checkpoint(mode: str = Query("TRUNCATE", description="PASSIVE, FULL, RESTART, TRUNCATE")):
    """手動執行 SQLite WAL Checkpoint"""
    from core.data_lifecycle import checkpoint_sqlite_database
    return checkpoint_sqlite_database(mode=mode)


@app.post("/api/v1/system/heal")
def trigger_system_heal():
    """主動檢查所有背景採集器與排程器，若發現異常中斷自動執行自我修復 (Self-Healing)"""
    manager = get_manager()
    return manager.supervise_and_heal()


@app.get("/api/v1/system/health")
def get_system_health():
    """全域系統健康診斷端點：整合採集器診斷、自我修復狀態、維護收據與資料庫指標"""
    from core.data_lifecycle import get_latest_maintenance_receipt, configured_database_path
    manager = get_manager()
    status = manager.get_status()
    receipt = get_latest_maintenance_receipt()
    db_path = configured_database_path()
    wal_path = Path(str(db_path) + "-wal")
    db_size = db_path.stat().st_size if db_path.is_file() else 0
    wal_size = wal_path.stat().st_size if wal_path.is_file() else 0

    return {
        "status": status.get("monitoring_state", "unknown"),
        "is_running": status.get("is_running", False),
        "degraded_collectors": status.get("degraded_collectors", []),
        "watchers": status.get("watchers", {}),
        "collector_runtime": status.get("collector_runtime", {}),
        "collector_health": status.get("collector_health", {}),
        "collector_diagnostics": status.get("collector_diagnostics", {}),
        "self_healing": status.get("self_healing", {}),
        "database": {
            "path": str(db_path),
            "size_bytes": db_size,
            "wal_size_bytes": wal_size,
            # Health check 只讀取已物化的狀態；不可因輪詢而觸發全量掃描與寫入。
            "active_projects_count": get_project_state_count(),
        },
        "latest_maintenance": receipt,
        "database_migration": status.get("database_migration", {}),
        "metrics": status.get("metrics", {}),
        "timestamp": get_local_now().isoformat(),
    }


class OpenPathRequest(BaseModel):
    path: Optional[str] = None
    action: Optional[str] = "explorer"  # "explorer" | "vscode" | "terminal" | "browser"
    url: Optional[str] = None


@app.post("/api/v1/control/open_path")
def open_system_path(payload: OpenPathRequest):
    """在宿主機直接開啟本機資料夾、VS Code、終端機或指定網頁"""
    try:
        if payload.url:
            open_web_url(payload.url)
            return {"status": "success", "message": f"已在瀏覽器開啟: {payload.url}"}
        if not payload.path:
            raise ValueError("必須提供 path 或 url")
        open_local_path(payload.path, payload.action or "explorer")
        return {"status": "success", "message": f"已執行 {payload.action}: {payload.path}"}
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


@app.get("/api/v1/projects/{project_key}/handoff")
def get_project_handoff_api(
    project_key: str,
    turns: int = Query(5, ge=1, le=20, description="納入之歷史 AI 對話回合數")
):
    """取得指定專案的 Context Handoff 結構化接續 Prompt (P3-1)"""
    from core.handoff_engine import build_project_handoff, format_handoff_markdown
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


@app.get("/api/v1/open-loops")
def get_open_loops(project: Optional[str] = None, status: str = "open"):
    """取得未結事項清單"""
    statuses = {item.strip().lower() for item in status.split(",") if item.strip()}
    try:
        return get_open_loops_list(project_key=project, statuses=statuses)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/open-loops/{loop_id}/resolve")
def resolve_open_loop(loop_id: int):
    """將未結事項標記為已解決"""
    try:
        result = transition_open_loop(loop_id, "resolved", "Resolved from dashboard")
        return {"status": "success", "transition": result}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class OpenLoopTransitionRequest(BaseModel):
    status: str = Field(..., description="open, stale, resolved, superseded")
    note: Optional[str] = None


@app.post("/api/v1/open-loops/{loop_id}/transition")
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


# =====================================================================
# 3. 配置動態讀寫 API
# =====================================================================
@app.get("/api/v1/config")
def get_system_config():
    cfg = get_config()
    public_config = redact_config(cfg.data)

    # 即使是首次啟動、尚未建立 config.yaml，也維持安全相關欄位的
    # 固定回應結構。前端因此能明確區分「未設定」與「API 契約缺失」，
    # 同時不會洩漏任何 secret。
    public_config.setdefault("integrations", {}).setdefault("github", {}).setdefault(
        "token", ""
    )
    public_config.setdefault("security", {}).setdefault(
        "browser_extension_ingest_token", ""
    )
    return public_config


@app.post("/api/v1/config")
def update_system_config(new_config: Dict[str, Any] = Body(...)):
    try:
        cfg = get_config()
        merged_config = merge_redacted_config(cfg.data, new_config)
        cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(merged_config, f, allow_unicode=True, sort_keys=False)
        
        manager = get_manager()
        manager.reload_config()
        return {"status": "success", "message": "配置更新成功並已套用至監控引擎"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


@app.get("/api/v1/llm/status")
def get_llm_secret_status():
    """Report provider credential availability without returning secret values."""
    cfg = get_config()
    providers = {}
    definitions = {
        "gemini": ("GEMINI_API_KEY", ("GOOGLE_API_KEY",)),
        "anthropic": ("ANTHROPIC_API_KEY", ()),
        "openai": ("OPENAI_API_KEY", ()),
    }
    for provider, (default_env, aliases) in definitions.items():
        env_name = str(
            cfg.get(f"synthesizer.{provider}.api_key_env", default_env)
            or default_env
        )
        providers[provider] = resolve_secret_env(env_name, aliases).public_status()

    providers["ollama"] = {
        "configured": True,
        "source": "local_service",
        "env_var": "",
    }
    selected = str(cfg.get("synthesizer.provider", "gemini") or "gemini").lower()
    return {
        "selected_provider": selected,
        "providers": providers,
        "secret_boundary": "status_only_no_secret_values",
    }


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
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported browser platform: {plat}")

    if not browser_enabled:
        return {"status": "skipped", "message": f"{payload.platform} monitoring is disabled in settings"}

    db = get_db()
    clean_prompt = payload.prompt_text.strip()
    if len(clean_prompt) < 2:
        raise HTTPException(status_code=422, detail="prompt_text is too short")
    clean_response = (payload.response_text or "").strip() or None
    now = get_local_now()
    conversation_key = browser_conversation_key(payload.conversation_id, payload.url)
    capture_state = str((payload.metadata or {}).get("capture_state", "")).lower()
    response_status = browser_response_status(clean_response, capture_state)

    with db.session_scope() as session:
        # 尋找最近 10 分鐘內、相同平台與 Prompt 的記錄 (Upsert 邏輯)
        recent_cutoff = get_local_now() - timedelta(minutes=10)
        existing = (
            session.query(AIPromptEvent)
            .filter(
                AIPromptEvent.platform == payload.platform,
                AIPromptEvent.conversation_id == conversation_key,
                AIPromptEvent.prompt_text == clean_prompt,
                AIPromptEvent.timestamp >= recent_cutoff
            )
            .order_by(AIPromptEvent.timestamp.desc())
            .first()
        )

        if existing:
            # 若已有記錄且新 payload 帶有回應內容，則更新回應
            if clean_response:
                existing.response_text = clean_response
                existing.response_status = response_status
                if payload.url: existing.url = payload.url
                if payload.project_tag: existing.project_tag = payload.project_tag
                if payload.cwd: existing.cwd = payload.cwd
            return {"status": "updated", "message": "Existing AI event updated with response"}

        # 否則新增記錄
        bucket = int(now.timestamp() // 600)
        browser_turn_key = hashlib.sha256(
            f"browser|{plat}|{conversation_key}|{clean_prompt}|{bucket}".encode("utf-8")
        ).hexdigest()
        event = AIPromptEvent(
            platform=payload.platform,
            url=payload.url,
            conversation_id=conversation_key,
            prompt_text=clean_prompt,
            response_text=clean_response,
            project_tag=payload.project_tag,
            cwd=payload.cwd,
            metadata_json=json.dumps(payload.metadata, ensure_ascii=False) if payload.metadata else None,
            timestamp=now,
            turn_key=browser_turn_key,
            source_path=payload.url,
            response_status=response_status,
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


class RepositorySyncActionRequest(BaseModel):
    """本機 Git 寫入動作必須以已列出的 repo_id 與明確確認發出。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repo_id: str = Field(..., pattern=r"^[a-f0-9]{16}$")
    action: Literal["fetch", "pull_ff_only", "push", "commit_staged"]
    confirmation: Literal["confirmed"]
    commit_message: Optional[str] = Field(default=None, max_length=300)


# =====================================================================
# 9. GitHub 雲端專案與 PR 智慧追蹤 API (GitHub Cloud Integration)
# =====================================================================
@app.get("/api/v1/repos/sync-status")
def get_local_repository_sync_status():
    """列出設定 root 內的本機 Git 狀態。

    ahead/behind 只比較目前本機保存的 remote-tracking ref；不會在載入頁面時
    自動連線、fetch 或改動任何 worktree。
    """
    return LocalRepositorySync().list_statuses()


@app.post("/api/v1/repos/sync-action")
def run_local_repository_sync_action(req: RepositorySyncActionRequest):
    """逐一執行已確認的 fetch / fast-forward pull / staged commit / push。"""
    try:
        return LocalRepositorySync().execute(
            repo_id=req.repo_id,
            action=req.action,
            commit_message=req.commit_message,
        )
    except RepositorySyncRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.config_path, "w", encoding="utf-8") as f:
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
        cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.config_path, "w", encoding="utf-8") as f:
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

