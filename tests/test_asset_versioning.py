"""前端資產 cache-buster 契約：index.html 的 ?v= 必須跟著前端內容變。

2026-09-02 的分頁編號重複就是舊 app.js 被瀏覽器快取、以舊 i18n 字典覆蓋新 HTML
造成的；寫死的版本字串救不了這種情況。

D10 之後前端是 `web/js/` 的 ES module 樹 ＋ `web/i18n/` 的語系 JSON——雜湊要涵蓋整棵樹，
否則改了某個分頁模組而進入點的 ?v= 沒變，使用者會拿到新舊混搭的模組。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from core import __version__
from core.server import app, asset_version, render_index_html

_LOCAL_ORIGIN = "http://127.0.0.1:8765"


def test_index_html_has_no_hardcoded_asset_version():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count("?v=__ASSET_VERSION__") == 3  # style.css、js/main.js、vendor/marked.min.js
    assert "?v=1.3.0a" not in html  # 不得再寫死版本字串


def test_asset_version_tracks_every_frontend_file(tmp_path: Path):
    """整棵 module 樹與語系檔都要算進雜湊——只看進入點會漏掉改了分頁模組的情況。"""
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "js" / "tabs").mkdir(parents=True)
    (tmp_path / "i18n").mkdir()
    (tmp_path / "js" / "main.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "js" / "tabs" / "projects.js").write_text("export const a = 1;", encoding="utf-8")
    (tmp_path / "i18n" / "zh-TW.json").write_text('{"a": "甲"}', encoding="utf-8")
    first = asset_version(tmp_path)
    assert first.startswith(f"{__version__}-")

    # 只改一個分頁模組（不動進入點）也必須換版本
    (tmp_path / "js" / "tabs" / "projects.js").write_text("export const a = 2;", encoding="utf-8")
    second = asset_version(tmp_path)
    assert second != first

    # 只改語系檔也必須換版本
    (tmp_path / "i18n" / "zh-TW.json").write_text('{"a": "乙"}', encoding="utf-8")
    assert asset_version(tmp_path) != second


def test_served_index_substitutes_version_and_disables_html_caching():
    client = TestClient(app)
    res = client.get("/", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200
    assert "__ASSET_VERSION__" not in res.text
    assert f"/static/js/main.js?v={asset_version()}" in res.text
    assert f"/static/style.css?v={asset_version()}" in res.text
    assert res.headers.get("cache-control") == "no-cache"
    assert render_index_html() is not None


def test_index_html_loads_no_external_assets():
    """local-first（ADR-001）：儀表板不得對外載入任何 script／stylesheet／font。

    2026-09-16 前 index.html 從 jsdelivr 載入 marked、從 Google Fonts 載入字型——
    離線就壞，且每次開頁都對外發請求（TODO B8）。
    """
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    for tag in ("<script", "<link"):
        for chunk in html.split(tag)[1:]:
            head = chunk.split(">", 1)[0]
            assert "http://" not in head and "https://" not in head, f"{tag}{head}"
    assert "/static/vendor/marked.min.js?v=__ASSET_VERSION__" in html


def test_vendored_marked_is_served_locally():
    client = TestClient(app)
    res = client.get("/static/vendor/marked.min.js", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200
    assert "marked" in res.text[:400]
