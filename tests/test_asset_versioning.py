"""前端資產 cache-buster 契約：index.html 的 ?v= 必須跟著 app.js／style.css 內容變。

2026-09-02 的分頁編號重複就是舊 app.js 被瀏覽器快取、以舊 i18n 字典覆蓋新 HTML
造成的；寫死的版本字串救不了這種情況。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from core import __version__
from core.server import app, asset_version, render_index_html

_LOCAL_ORIGIN = "http://127.0.0.1:8765"


def test_index_html_has_no_hardcoded_asset_version():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count("?v=__ASSET_VERSION__") == 2
    assert "?v=1.3.0a" not in html  # 不得再寫死版本字串


def test_asset_version_tracks_file_content(tmp_path: Path):
    (tmp_path / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    first = asset_version(tmp_path)
    assert first.startswith(f"{__version__}-")
    (tmp_path / "app.js").write_text("console.log(2)", encoding="utf-8")
    assert asset_version(tmp_path) != first


def test_served_index_substitutes_version_and_disables_html_caching():
    client = TestClient(app)
    res = client.get("/", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200
    assert "__ASSET_VERSION__" not in res.text
    assert f"/static/app.js?v={asset_version()}" in res.text
    assert f"/static/style.css?v={asset_version()}" in res.text
    assert res.headers.get("cache-control") == "no-cache"
    assert render_index_html() is not None
