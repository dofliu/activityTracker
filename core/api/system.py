"""健康、採集器控制、系統維運與驗收中心（TODO D4，ROADMAP §13 R1）。

health／extension 狀態與心跳／capture 覆蓋／control start-stop／system 維護與自我修復／acceptance 檢查表。
這一組的共通點是「關於 OmniContext 自己」，不是使用者的工作資料。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import logging
from core.capture_coverage import build_capture_coverage
from core.config import get_config
from core.extension_monitor import build_extension_status, record_extension_heartbeat
from core.extension_verification import extension_verification_registry
from core.manager import get_manager
from core.platform_services import open_local_path, open_web_url
from core.project_engine import get_project_state_count
from core.runtime_paths import is_demo_home
from core.schemas import AcceptanceConfirmRequest, ExtensionHeartbeatCreate, ExtensionVerificationStart, OpenPathRequest, SystemMaintenanceRequest
from core.security import extension_ingest_authorized
from core.time_utils import get_local_now
from fastapi import APIRouter, Body, HTTPException, Query, Request
from pathlib import Path
from typing import Optional
from core.data_lifecycle import checkpoint_sqlite_database, configured_database_path, get_latest_maintenance_receipt, run_database_maintenance
from core.acceptance import build_acceptance_report, record_human_confirmation

logger = logging.getLogger("OmniContext.Server")

router = APIRouter()


@router.get("/api/v1/health")
def health_check():
    return {
        "status": "ok",
        "service": "OmniContext",
        "time": get_local_now().isoformat(),
        # ADR-031：目前 home 是不是 `omni demo` 建立的示範家目錄——只看旗標檔，不用猜。
        "demo_mode": is_demo_home(),
    }


@router.get("/api/v1/extension/status")
def get_extension_monitor_status(request: Request):
    """Dashboard 可看觀測狀態；Extension 帶 token 時另可驗證 pairing。"""
    return build_extension_status(
        request.headers.get("x-omnicontext-ingest-token")
    )


@router.post("/api/v1/extension/heartbeat", status_code=202)
def receive_extension_heartbeat(payload: ExtensionHeartbeatCreate, request: Request):
    """只接受帶正確 ingest token 的非敏感 Extension heartbeat。"""
    cfg = get_config()
    token = request.headers.get("x-omnicontext-ingest-token")
    if not extension_ingest_authorized(token, cfg):
        raise HTTPException(status_code=401, detail="Extension ingest token is invalid")
    return record_extension_heartbeat(payload.model_dump())


@router.post("/api/v1/extension/verification", status_code=201)
def start_extension_verification(payload: ExtensionVerificationStart):
    """建立 process-local baseline；不保存 token、對話內容或 verification run。"""
    try:
        return extension_verification_registry.start(
            payload.platforms,
            timeout_seconds=payload.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/extension/verification/{verification_id}")
def get_extension_verification(verification_id: str):
    try:
        return extension_verification_registry.get(verification_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Verification run not found") from exc


@router.get("/api/v1/capture/status")
def get_capture_status():
    """分開回傳 focus、web 與 transcript coverage，避免以單一 ONLINE 誤導。"""
    return build_capture_coverage()


@router.get("/api/v1/control/status")
def get_control_status():
    manager = get_manager()
    return manager.get_status()


@router.post("/api/v1/control/start")
def start_monitoring():
    manager = get_manager()
    return manager.start_all()


@router.post("/api/v1/control/stop")
def stop_monitoring():
    manager = get_manager()
    return manager.stop_all()


@router.post("/api/v1/system/maintenance")
def trigger_system_maintenance(payload: SystemMaintenanceRequest = Body(default_factory=SystemMaintenanceRequest)):
    """手動觸發資料庫生命週期維護（Checkpoint、完整性檢查、歷史修剪、線上備份、輪替）"""
    res = run_database_maintenance(
        max_backups=payload.max_backups or 7,
        retention_days=payload.retention_days or 90,
        dry_run=payload.dry_run or False,
    )
    return res


@router.get("/api/v1/system/maintenance/receipt")
def get_system_maintenance_receipt():
    """取得最近一次資料庫維護收據與健康資訊"""
    receipt = get_latest_maintenance_receipt()
    if not receipt:
        return {"has_receipt": False, "status": "no_receipt", "message": "尚未執行過資料庫維護"}
    return {"has_receipt": True, "receipt": receipt, **receipt}


@router.post("/api/v1/system/wal-checkpoint")
def trigger_wal_checkpoint(mode: str = Query("TRUNCATE", description="PASSIVE, FULL, RESTART, TRUNCATE")):
    """手動執行 SQLite WAL Checkpoint"""
    return checkpoint_sqlite_database(mode=mode)


@router.post("/api/v1/system/heal")
def trigger_system_heal():
    """主動檢查所有背景採集器與排程器，若發現異常中斷自動執行自我修復 (Self-Healing)"""
    manager = get_manager()
    return manager.supervise_and_heal()


@router.get("/api/v1/system/health")
def get_system_health():
    """全域系統健康診斷端點：整合採集器診斷、自我修復狀態、維護收據與資料庫指標"""
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


@router.get("/api/v1/acceptance/checklist")
def get_acceptance_checklist(item: Optional[str] = Query(None, max_length=100)):
    """驗收中心：docs/TODO.md A 段每一項的本機收據現況。

    唯讀且刻意便宜——只查 SQLite、設定與檔案是否存在，不跑 git、不連網、
    不載入索引。runtime=True 因為這裡就是服務程序，檢索 worker 這類
    記憶體狀態只有在這個程序內才看得到。
    """

    only = [part.strip() for part in item.split(",") if part.strip()] if item else None
    return build_acceptance_report(runtime=True, only=only)


@router.post("/api/v1/acceptance/confirm")
def confirm_acceptance_item(payload: AcceptanceConfirmRequest):
    """記下「我親眼確認過這一項」。

    這是人工署名收據，不是機器證據：只讓機器沒有判準可查的項目收斂，
    永遠不會覆蓋機器已查到的結果。沒有外部效果，沿用 loopback 邊界即可。
    """

    try:
        return record_human_confirmation(
            payload.item_id, confirmed=payload.confirmed, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unknown_acceptance_item") from exc


@router.post("/api/v1/control/open_path")
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
