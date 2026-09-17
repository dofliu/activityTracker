"""儀表板頁面與前端資產版本（TODO D4，ROADMAP §13 R1）。

index.html／extension-monitor.html 的供應，以及 `?v=` cache-buster 的計算。
資產雜湊邏輯放這裡，靜態目錄掛載仍在 `core/server.py`（那是 app 組裝的事）。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import hashlib

from core import __version__
from core.runtime_paths import web_assets_dir
from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from pathlib import Path


# Web 靜態資源目錄：頁面與資產雜湊都以它為準；`core/server.py` 掛載 /static 時也用同一個值。
WEB_DIR = web_assets_dir()
if not WEB_DIR.is_dir():
    raise RuntimeError(f"OmniContext Web assets are missing: {WEB_DIR}")

router = APIRouter()


def asset_version(web_dir: Path | None = None) -> str:
    """前端資產的 cache-buster：版本號＋ app.js/style.css 內容雜湊。

    以前 index.html 寫死 ``?v=1.3.0a12-…``，每次改 app.js 瀏覽器都可能沿用
    舊快取（例：分頁編號重複，是舊 app.js 的 i18n 字典覆蓋了新 HTML）。
    雜湊由檔案內容算出，任何一次改動都會換 URL，不必手動記得改版本字串。
    """
    web_dir = web_dir or WEB_DIR
    digest = hashlib.sha1()
    # D10 之後前端是一棵 ES module 樹（web/js/）＋ 兩份語系 JSON，不再是單一 app.js。
    # 任何一個檔案改動都要換掉進入點的 ?v=，否則使用者可能拿到新舊混搭的模組。
    sources = [web_dir / "style.css"]
    for folder in ("js", "i18n"):
        sources.extend(sorted((web_dir / folder).rglob("*")) if (web_dir / folder).is_dir() else [])
    for source in sources:
        if source.is_dir():
            continue
        try:
            digest.update(source.name.encode("utf-8"))
            digest.update(source.read_bytes())
        except OSError:
            digest.update(str(source).encode("utf-8"))
    return f"{__version__}-{digest.hexdigest()[:10]}"


def render_index_html(web_dir: Path | None = None) -> str | None:
    web_dir = web_dir or WEB_DIR
    index_file = web_dir / "index.html"
    if not index_file.exists():
        return None
    return index_file.read_text(encoding="utf-8").replace("__ASSET_VERSION__", asset_version(web_dir))


@router.get("/", response_class=HTMLResponse)
def index_page():
    html = render_index_html()
    if html is not None:
        # HTML 本體不快取，資產靠 ?v= 雜湊換 URL；兩者合起來才不會出現新舊混用。
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h2>OmniContext Web Dashboard is initializing... Please refresh shortly.</h2>")


@router.get("/extension-monitor", response_class=HTMLResponse)
def extension_monitor_page():
    monitor_file = WEB_DIR / "extension-monitor.html"
    if monitor_file.exists():
        return FileResponse(str(monitor_file))
    return HTMLResponse("<h2>OmniContext Extension Monitor is initializing...</h2>")
