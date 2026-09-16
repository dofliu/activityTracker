"""設定讀寫與工具（TODO D4，ROADMAP §13 R1）。

config 的讀取（遮蔽 secret）與寫入（merge 既有 secret），以及資料夾瀏覽工具。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import yaml

from core.config import get_config
from core.manager import get_manager
from core.schemas import BrowseFolderRequest
from core.security import merge_redacted_config
from core.security import redact_config
from fastapi import APIRouter
from fastapi import Body
from fastapi import HTTPException
from typing import Any
from typing import Dict
from typing import Optional


router = APIRouter()


@router.get("/api/v1/config")
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


@router.post("/api/v1/config")
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


@router.post("/api/v1/utils/browse-folder")
def api_browse_folder(req: Optional[BrowseFolderRequest] = None):
    """彈出本機原生資料夾選擇對話框"""
    from .fs_utils import open_native_folder_picker
    init_dir = req.initial_dir if req else None
    chosen = open_native_folder_picker(initial_dir=init_dir)
    if chosen:
        return {"status": "success", "path": chosen}
    return {"status": "cancelled", "path": None}
