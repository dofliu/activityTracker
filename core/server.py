"""FastAPI app 的組裝：安全邊界、靜態資源與 router 掛載（TODO D4，ROADMAP §13 R1）。

2026-09-16 之前這個檔案是 1,995 行、98 條路由、33 個 Pydantic model 混在一起——
要找一個端點得先捲過三十幾個 model 宣告，而「這支 API 屬於哪個領域」只能靠讀路徑猜。
現在它只做四件事：

1. 建立 app 並掛上本機安全邊界 middleware（ADR-001：loopback、Origin allowlist、
   Extension 的 write-only ingest token）。
2. 掛載 `/static` 靜態資源。
3. 依領域掛上 9 個 router（`core/api/*.py`）＋ DeskRAG 子系統。
4. 為既有呼叫端保留 `asset_version`／`render_index_html`／`WEB_DIR` 的名稱。

**路由表本身沒有任何改變**——路徑、方法與 handler 名稱由
`tests/test_api_route_snapshot.py` 的 133 條快照鎖住，少一條多一條都會失敗。
請求結構在 `core/schemas.py`，AI 事件的去重與狀態判定在 `core/ingest.py`。
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import activity, events, integrations, pages, projects, repos, secretary, settings, system
from .api.pages import WEB_DIR, asset_version, render_index_html  # noqa: F401 — 既有呼叫端沿用這些名稱
from .config import get_config
from .security import (
    configured_allowed_origins,
    extension_ingest_authorized,
    is_extension_origin,
    is_loopback_host,
    origin_is_allowed,
)
from rag.router import router as rag_router

logger = logging.getLogger("OmniContext.Server")


class DynamicCorsMiddleware(CORSMiddleware):
    """每個請求看**當下**的 allowlist（ADR-027，TODO D11）。

    D11 之前這個清單在 import 時就算好、凍結在 middleware 裡：使用者在儀表板改了
    `security.allowed_origins` 並存檔，下面的 `enforce_local_security_boundary`（每個請求
    重讀設定）立刻生效、CORS 標頭卻還是舊的，**必須重啟服務**——而且沒有任何地方寫著這件事。

    只有在清單真的變了的時候才用公開建構子重算一次衍生標頭；沒變就只是一次 list 比較。
    邊界沒有放寬：允許清單仍然只來自設定檔，loopback 限制與 extension token 邊界原封不動，
    改的只是「什麼時候讀」。
    """

    def __init__(self, app, **options):
        self._cors_options = options
        super().__init__(app, **options)
        self._origins_snapshot = list(options.get("allow_origins") or ())

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            current = configured_allowed_origins(get_config())
            if current != self._origins_snapshot:
                options = {**self._cors_options, "allow_origins": current}
                CORSMiddleware.__init__(self, self.app, **options)
                self._cors_options = options
                self._origins_snapshot = list(current)
                logger.info("CORS allowlist reloaded from config (%d origin(s)).", len(current))
        await super().__call__(scope, receive, send)

app = FastAPI(
    title="OmniContext Local Engine & Web Dashboard",
    description="個人全景上下文與活動記憶核心 API 與 Web 儀表板",
    version=__version__
)

# 僅允許本機 dashboard origins；browser extension 走獨立 write-only token boundary。
# 清單每個請求對照一次當下設定（DynamicCorsMiddleware），改設定不必重啟。
app.add_middleware(
    DynamicCorsMiddleware,
    allow_origins=configured_allowed_origins(get_config()),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_EXTENSION_TOKEN_WARNING_STATE: dict[str, Any] = {"last_logged": 0.0, "suppressed": 0}


def _warn_extension_token_mismatch(token_present: bool) -> None:
    """每 60 秒最多一則 WARNING，附上這段期間被壓掉的次數，避免 log 被每秒重送淹沒。"""
    import time as _time

    now = _time.monotonic()
    state = _EXTENSION_TOKEN_WARNING_STATE
    if now - float(state["last_logged"]) < 60:
        state["suppressed"] = int(state["suppressed"]) + 1
        return
    suppressed = int(state["suppressed"])
    state["last_logged"] = now
    state["suppressed"] = 0
    logger.warning(
        "Browser Extension 的寫入請求被拒（403）：ingest token %s。%s"
        "請在 Extension popup 重新貼上 `omnicontext init` 顯示的 ingest token；"
        "在此之前 extension 的離線佇列會持續重送。",
        "不符" if token_present else "缺少",
        f"（過去 60 秒另有 {suppressed} 次相同拒絕）" if suppressed else "",
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
            # Extension 帶了 origin 但 token 缺少／不符：console 只印一行 403 看不出原因，
            # 這裡給明確 detail，並以節流的 WARNING 說明（extension 的離線佇列會每秒重送）。
            _warn_extension_token_mismatch(bool(token))
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "extension ingest token missing" if not token else "extension ingest token mismatch",
                    "hint": "Extension popup 的 token 需與 security.browser_extension_ingest_token（或其環境變數）一致；請重新貼上 `omnicontext init` 顯示的 ingest token。",
                },
            )
        return JSONResponse(status_code=403, content={"detail": "Origin is not allowed"})

    return await call_next(request)


# 掛載 Web 靜態資源目錄（WEB_DIR 由 core/api/pages.py 定義並在缺檔時 fail-closed）
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# 依領域掛上各 router；順序不影響比對（路徑互斥），但維持與 core/api/ 的閱讀順序一致。
app.include_router(pages.router)
app.include_router(system.router)
app.include_router(events.router)
app.include_router(activity.router)
app.include_router(secretary.router)
app.include_router(projects.router)
app.include_router(repos.router)
app.include_router(integrations.router)
app.include_router(settings.router)
# DeskRAG 本地知識庫子系統
app.include_router(rag_router)
