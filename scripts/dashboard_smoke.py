#!/usr/bin/env python3
"""儀表板煙霧測試：六個分頁載入、渲染、切語言，且不吵（ADR-026，TODO D10）。

D10 把 `web/app.js`（6,096 行）拆成 ES module 樹。「拆完還能跑」不是靠讀程式碼確認的，
所以這支腳本用真的瀏覽器開真的服務，檢查四件事：

1. **六個分頁都切得過去**，而且切過去之後那一頁是可見的。
2. **console 沒有錯誤、沒有未捕捉的例外**——ES module 的匯入錯誤與未定義識別字都會在這裡現形。
   只採計**本站資源**：容器裡的 TLS proxy 會擋掉 GitHub 頭像之類的外部圖片，那不是本專案的問題。
3. **字典真的載進來了**：語言按鈕的文字必須等於 `web/i18n/*.json` 裡的 `lang_btn`，
   而且按一下要真的切成另一種語言（D10 之前字典是程式裡的物件，現在是 fetch 進來的資料）。
4. **1440px 與 494px 都沒有頁面水平溢出**（沿用 2026-09 以來每一輪 UI 改動的同一條判準）。

用法：`python scripts/dashboard_smoke.py [base_url]`（預設 http://127.0.0.1:8765）。
需要 playwright 與一個 Chromium；`OMNI_CHROMIUM` 可指定執行檔路徑。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

TABS = ["tab-assistant", "tab-knowledge", "tab-projects", "tab-repos", "tab-summaries", "tab-settings"]
VIEWPORTS = ((1440, "desktop"), (494, "narrow"))
REPO_ROOT = Path(__file__).resolve().parents[1]


def run(base_url: str) -> dict:
    from playwright.sync_api import sync_playwright

    zh = json.loads((REPO_ROOT / "web" / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
    en = json.loads((REPO_ROOT / "web" / "i18n" / "en.json").read_text(encoding="utf-8"))
    host = urlparse(base_url).netloc

    report: dict = {"console_errors": [], "page_errors": [], "overflow": {},
                    "tabs_visible": {}, "i18n": {}, "external_blocked": []}
    launch: dict = {}
    executable = os.environ.get("OMNI_CHROMIUM") or "/opt/pw-browsers/chromium"
    if Path(executable).exists():
        launch["executable_path"] = executable

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        for width, label in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": 900})

            def on_console(msg, label=label):
                if msg.type != "error":
                    return
                where = (msg.location or {}).get("url", "")
                # 只採計本站資源；外部圖片被容器的 TLS proxy 擋掉不是本專案的問題
                if where and host not in where:
                    report["external_blocked"].append(f"[{label}] {where}")
                    return
                report["console_errors"].append(f"[{label}] {msg.text}")

            def on_request_failed(req, label=label):
                if host in req.url:
                    report["console_errors"].append(f"[{label}] request failed: {req.url}")
                else:
                    report["external_blocked"].append(f"[{label}] {req.url}")

            page.on("console", on_console)
            page.on("pageerror", lambda e, label=label: report["page_errors"].append(f"[{label}] {e}"))
            page.on("requestfailed", on_request_failed)

            page.goto(base_url, wait_until="networkidle")

            # 3. 字典載入與切換（只在桌面寬度做一次就夠）
            if label == "desktop":
                page.wait_for_function(
                    "text => document.getElementById('btn-lang')?.textContent.trim() === text",
                    arg=zh["lang_btn"], timeout=5000,
                )
                report["i18n"]["zh_applied"] = True
                page.click("#btn-lang")
                page.wait_for_function(
                    "text => document.getElementById('btn-lang')?.textContent.trim() === text",
                    arg=en["lang_btn"], timeout=5000,
                )
                report["i18n"]["switch_to_en"] = True
                page.click("#btn-lang")   # 切回中文，後面的分頁檢查用預設語言
                page.wait_for_timeout(200)

            # 1 + 4. 分頁與溢出
            for tab in TABS:
                page.click(f'.tab[data-tab="{tab}"]')
                page.wait_for_timeout(350)
                report["tabs_visible"][f"{label}:{tab}"] = page.is_visible(f"#{tab}")
                box = page.evaluate(
                    "() => ({scroll: document.documentElement.scrollWidth,"
                    " client: document.documentElement.clientWidth})"
                )
                if box["scroll"] > box["client"] + 1:
                    report["overflow"][f"{label}:{tab}"] = box
            page.close()
        browser.close()

    report["ok"] = bool(
        not report["console_errors"]
        and not report["page_errors"]
        and not report["overflow"]
        and all(report["tabs_visible"].values())
        and report["i18n"].get("zh_applied")
        and report["i18n"].get("switch_to_en")
    )
    return report


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
    report = run(base_url)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
