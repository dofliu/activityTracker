#!/usr/bin/env python3
"""前端行為鎖：六個分頁在固定資料下的渲染結果，逐位元組釘住（ADR-029，TODO D11 續）。

## 為什麼需要它

D11（ADR-027）把後端的程序內可變狀態收成可注入的 store，但**前端的 `state.js` 沒動**。
理由寫在 ADR-027 的 Consequences：前端 49 個共享值全部經由渲染函式的閉包讀寫，要改成注入
等於重寫十個模組的函式簽章，而**沒有等價於 pytest 的東西可以證明「行為不變」**。
`scripts/dashboard_smoke.py` 證明的是「開得起來、不吵、不溢出」，不是「畫出來的東西一樣」。

這支腳本就是那個缺的證據，做法照 D12 差分收據的同一條路：**固定輸入 → 完整輸出 → 逐位元組比對**。

## 它鎖什麼

1. **輸入固定**：所有 `/api/**` 回應來自 `tests/fixtures/dashboard_dom/api/`。不碰資料庫、
   不連網、不需要跑伺服器；靜態檔直接從 `web/` 讀。
2. **時鐘固定**：瀏覽器的 `Date` 凍在 `meta.json` 的 `frozen_clock`，時區與語系也釘死，
   所以「3 天前」永遠是「3 天前」。`Math.random` 換成固定序列。
3. **輪詢關掉**：`setInterval` 在鎖裡回傳遞增的假 id 但不排程。焦點輪播與四個輪詢迴圈
   因此不會讓快照抖動。
4. **輸出**：六個分頁 × 兩種語言的 `innerHTML`，**一個字元都不動**地存進
   `tests/fixtures/dashboard_dom/panes/`。不 normalize——normalize 會把差異藏起來，
   而這支工具的全部價值就是不藏。

## 它不鎖什麼（先講清楚，免得把它當成它不是的東西）

- **不鎖輪詢後的更新**：釘的是第一次渲染。`setInterval` 被關掉了。
- **不鎖互動**：只走「開頁 → 依序點六個分頁」。按鈕、表單、對話框的後續渲染不在裡面。
- **不鎖樣式**：比的是 DOM，不是像素。CSS 改了它不會紅。
- **不鎖後端**：API 回應是錄下來的罐頭。後端行為由 pytest 管。

## 用法

    python scripts/dashboard_dom_lock.py record   # 重錄 API 罐頭（會起一份臨時的種子資料庫）
    python scripts/dashboard_dom_lock.py update   # 重錄分頁快照（動前端之前先跑一次）
    python scripts/dashboard_dom_lock.py check    # 比對（動完跑；有差就印出差在哪並 exit 1）

需要 playwright 與一個 Chromium；`OMNI_CHROMIUM` 可指定執行檔路徑（預設 /opt/pw-browsers/chromium）。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "dashboard_dom"
API_DIR = FIXTURE_DIR / "api"
PANE_DIR = FIXTURE_DIR / "panes"
META_PATH = FIXTURE_DIR / "meta.json"

BASE_URL = "http://dom-lock.invalid/"
ASSET_VERSION = "domlock"
RECORD_ORIGIN = "http://127.0.0.1:8765"      # 錄製時給後端的 Origin（CORS 由 pytest 管，不在這裡測）
VIEWPORT = {"width": 1440, "height": 2400}    # 夠高，讓分頁一次畫完，不必捲動
# 後端的時間欄位是 naive datetime（core/time_utils.get_local_now），JS 的 `new Date("…")` 會
# 當成瀏覽器當地時間解析。兩邊要對得起來，瀏覽器時區就得等於錄製機器的本機時區；錄製在
# UTC 容器裡跑，所以這裡釘 UTC。釘死它同時也讓「幾小時前」這種相對時間永遠算得出同一個答案。
TIMEZONE_ID = "UTC"
LOCALE = "zh-TW"
LANGS = ("zh-TW", "en")
TABS = ("tab-assistant", "tab-knowledge", "tab-projects", "tab-repos", "tab-summaries", "tab-settings")

# 互動場景：開機之後再做幾個動作，拍第二張。
#
# 為什麼要有：ADR-029 明說那把鎖「只釘開機後的第一畫面，不釘互動」，並且寫下如果重構會動到
# 互動路徑就得先把互動加進來。D13（`state.js` 改注入）正是那種重構——共享值大多是被**互動**
# 讀寫的，只鎖第一畫面等於把最該證明的那部分放生。
#
# 挑的五個都是**唯讀**的（不 POST、不改資料庫），而且每一個都會讀寫至少一個共享值：
#   expandedProject／showAllProjects、summaryView、activeFilter＋recentEvents、
#   currentConfig 的設定分頁、currentRagSessionId＋ragChatHistory。
SCENES = (
    ("projects-expand", "tab-projects",
     (("click", "#projects-list .pitem .prow"),)),
    ("summaries-week", "tab-summaries",
     (("click", '.viewswitch .chip[data-view="week"]'),)),
    ("settings-feed-git", "tab-settings",
     (("click", '.settings-nav-item[data-pane="feed"]'), ("click", '.filters .chip[data-filter="git"]'))),
    ("settings-pane-llm", "tab-settings",
     (("click", '.settings-nav-item[data-pane="llm"]'),)),
    ("knowledge-session", "tab-knowledge",
     (("select-index", "#select-rag-session", 1),)),
)

# 錄製時把機器相關的路徑換掉，快照才不會綁在某一台機器上。
HOME_PLACEHOLDER = "/omni/home"
REPO_PLACEHOLDER = "/omni/checkout"

_SETTLE_MS = 1200          # networkidle 之後再等這麼久才拍
_STABILITY_MS = 600        # 拍完再等這麼久重拍一次，兩張必須一樣


# --------------------------------------------------------------------------------------
# 共用：檔名、靜態檔、瀏覽器
# --------------------------------------------------------------------------------------

def slug_for(path_and_query: str) -> str:
    """把 `/api/v1/events/recent?limit=60&event_type=all` 變成一個穩定的檔名。"""
    cleaned = path_and_query[len("/api/"):] if path_and_query.startswith("/api/") else path_and_query
    return re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned).strip("_") + ".json"


def chromium_path() -> str | None:
    candidate = os.environ.get("OMNI_CHROMIUM") or "/opt/pw-browsers/chromium"
    return candidate if Path(candidate).exists() else None


def static_response(path: str) -> tuple[int, str, str] | None:
    """`/` 與 `/static/**` 從 checkout 直接讀；回傳 (status, content_type, body)。"""
    if path in ("/", "/index.html"):
        body = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__ASSET_VERSION__", ASSET_VERSION)
        return 200, "text/html; charset=utf-8", body
    if not path.startswith("/static/"):
        return None
    target = (WEB_DIR / path[len("/static/"):]).resolve()
    if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
        return 404, "text/plain; charset=utf-8", "not found"
    ctype = "text/javascript" if target.suffix == ".js" else (mimetypes.guess_type(target.name)[0] or "text/plain")
    return 200, f"{ctype}; charset=utf-8", target.read_text(encoding="utf-8")


def init_script(lang: str, frozen_epoch_ms: int) -> str:
    """凍時鐘、凍亂數、關掉輪詢、預設語言。三種模式共用同一份，錄下來的才等於重播的。"""
    return f"""
    (() => {{
      const FIXED = {frozen_epoch_ms};
      const RealDate = Date;
      class LockedDate extends RealDate {{
        constructor(...args) {{ if (args.length === 0) {{ super(FIXED); }} else {{ super(...args); }} }}
        static now() {{ return FIXED; }}
      }}
      window.Date = LockedDate;

      let seed = 0x2f6e2b1;
      Math.random = () => {{ seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }};

      // 輪詢在鎖裡不跑，但 id 必須是真值：程式碼會用 `if (state.xTimer)` 判斷有沒有在輪詢。
      let timerId = 1;
      window.setInterval = () => timerId++;

      try {{ localStorage.setItem("omni-lang", {lang!r}); }} catch (_) {{}}
    }})();
    """


def new_page(browser, lang: str, frozen_epoch_ms: int):
    page = browser.new_page(viewport=VIEWPORT, timezone_id=TIMEZONE_ID, locale=LOCALE)
    page.add_init_script(init_script(lang, frozen_epoch_ms))
    return page


def apply_step(page, step) -> None:
    """做一個動作。找不到目標就**吵**——靜靜地跳過會讓場景快照退化成第一畫面的複本。"""
    kind, selector = step[0], step[1]
    if kind == "click":
        locator = page.locator(selector).first
        if locator.count() == 0:
            raise RuntimeError(f"場景步驟找不到目標：{selector}。這個場景現在是空轉，先修它再說。")
        locator.click()
    elif kind == "select-index":
        index = step[2]
        options = page.locator(f"{selector} option")
        if options.count() <= index:
            raise RuntimeError(f"{selector} 只有 {options.count()} 個選項，選不到第 {index} 個——場景空轉。")
        page.select_option(selector, index=index)
    else:
        raise AssertionError(f"不認得的場景步驟：{kind}")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(_SETTLE_MS)


def capture_pane(page, tab: str, steps: tuple = ()) -> str:
    """一次開機只拍一個分頁。

    一開始的寫法是「開一次、依序點六個分頁、各拍一張」，結果穩定性檢查當場抓到 tab-assistant
    在後面幾個分頁被點過之後又變了——也就是那張快照的內容取決於它是第幾個被拍的。與其去查
    是誰跨分頁改了誰（那是另一件事），不如讓每張快照都只回答一個乾淨的問題：
    **「開機之後打開這個分頁，畫出來是什麼？」** 代價是十二次開機，慢，但每張快照都獨立。
    """
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_timeout(_SETTLE_MS)
    page.click(f'button.tab[data-tab="{tab}"]')
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(_SETTLE_MS)
    for step in steps:
        apply_step(page, step)

    html = page.evaluate(f'document.getElementById({tab!r}).innerHTML')
    page.wait_for_timeout(_STABILITY_MS)
    again = page.evaluate(f'document.getElementById({tab!r}).innerHTML')
    if html != again:
        raise RuntimeError(
            f"{tab} 在靜置 {_STABILITY_MS}ms 之後還在變，這張快照不能當鎖。"
            "先找出還在跑的計時器或未完成的請求，不要把不穩的東西釘起來。"
        )
    return html


# --------------------------------------------------------------------------------------
# record：用一份臨時的種子資料庫，錄下前端開機時真的會問的每一個 API
# --------------------------------------------------------------------------------------

def seed_home(home: Path, now: datetime) -> None:
    """建一份**有資料**的臨時 home。空資料庫只走得到「什麼都沒有」那條分支，鎖不住東西。"""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "# dashboard_dom_lock 專用的最小設定：只釘會影響渲染的開關，其餘走預設。\n"
        "database:\n  db_path: omni_context.db\n"
        "data_lifecycle:\n  auto_backup_before_migration: false\n"
        f"  backups_dir: {(home / 'backups').as_posix()}\n",
        encoding="utf-8",
    )

    from core.database import get_db
    from core.models import (
        AIPromptEvent, BackgroundTaskRun, CalendarEvent, DailySummary, FileActivityEvent,
        GitActivityEvent, GitHubPREvent, GitHubRepoState, OpenLoop, ProjectState,
        RAGChatMessage, RAGChatSession, RAGIndexJob, RAGIndexedFile, RAGIndexedFolder,
        SecretaryNote, SecretaryScheduledTask, WindowEvent,
    )

    ago = lambda **kw: now - timedelta(**kw)

    # 會咬人的字串：第一版種子全是乖巧的中文，結果把 `esc()` 的 `&quot;` 拿掉，十二張快照
    # 一個字元都沒變——因為沒有任何一筆資料含雙引號。跳脫是 XSS 的第一道門，鎖不住它
    # 這支鎖就沒資格叫行為鎖。所以每一條會被渲染的資料通道都塞一筆帶 " ' < > & 的內容。
    # 模型名刻意用假的：真的模型字面值只准住在 core/llm_client.py（contract test 守著），
    # 而且鎖住真名等於讓「換預設模型」去動十二張快照。
    HOSTILE = "\"雙引號\" <b>粗體</b> & '單引號' <img src=x onerror=1>"

    with get_db().session_scope() as s:
        s.add_all([
            ProjectState(project_key="omnicontext", display_name="OmniContext", category="Coding",
                         last_activity_at=ago(minutes=12), status="active",
                         last_action_summary="把驗收中心改成一個 reading 加一張階梯表 " + HOSTILE),
            ProjectState(project_key="thesis-wind", display_name="離岸風電論文", category="Paper",
                         last_activity_at=ago(hours=6), status="idle",
                         last_action_summary="第三章圖表重畫"),
            ProjectState(project_key="side-notes", display_name="讀書筆記", category="Personal",
                         last_activity_at=ago(days=4), status="stale",
                         last_action_summary="整理 ADR 讀後感"),
        ])
        s.add_all([
            OpenLoop(project_key="omnicontext", title="前端 state.js 還沒改成注入 " + HOSTILE,
                     source_type="ai_dialogue", confidence=0.9, created_at=ago(days=1),
                     status="open", last_seen_at=ago(hours=2)),
            OpenLoop(project_key="thesis-wind", title="第四章的實驗數據還缺一組",
                     source_type="manual", confidence=1.0, created_at=ago(days=3),
                     status="open", last_seen_at=ago(days=1)),
        ])
        s.add_all([
            AIPromptEvent(timestamp=ago(minutes=8), platform="claude_code",
                          prompt_text="繼續做 D11，先補前端行為鎖 " + HOSTILE, response_text="好，先把鎖做出來。" + HOSTILE,
                          project_tag="omnicontext", cwd="/omni/checkout",
                          turn_key="lock-turn-1", response_status="final_candidate"),
            AIPromptEvent(timestamp=ago(hours=3), platform="chatgpt",
                          prompt_text="幫我看一下這段離岸風電的統計", response_text="這組數據的變異數偏高。",
                          project_tag="thesis-wind", turn_key="lock-turn-2", response_status="final_candidate"),
        ])
        s.add_all([
            FileActivityEvent(timestamp=ago(minutes=20), file_path="/omni/checkout/core/acceptance/items.py",
                              file_name="items.py", file_type=".py", action="modified",
                              size_bytes=18422, diff_summary="+120 −38", project_name="omnicontext"),
            FileActivityEvent(timestamp=ago(hours=5), file_path="/omni/papers/ch3.tex",
                              file_name="ch3.tex", file_type=".tex", action="modified",
                              size_bytes=90210, diff_summary="+42 字", project_name="thesis-wind"),
        ])
        s.add_all([
            GitActivityEvent(timestamp=ago(minutes=30), repo_name="activityTracker",
                             repo_path="/omni/checkout", commit_hash="a1b2c3d4e5f6", branch="main",
                             author="dofliu", message="refactor(acceptance): 一個 reading 加一張階梯表 " + HOSTILE,
                             files_changed_count=9, insertions=1505, deletions=1560),
            GitActivityEvent(timestamp=ago(days=2), repo_name="activityTracker",
                             repo_path="/omni/checkout", commit_hash="f6e5d4c3b2a1", branch="main",
                             author="dofliu", message="refactor(core): 程序內狀態收成可注入的 store",
                             files_changed_count=14, insertions=820, deletions=410),
        ])
        s.add_all([
            WindowEvent(start_time=ago(hours=2), end_time=ago(hours=1, minutes=10),
                        duration_seconds=3000.0, app_name="Code", window_title="items.py — activityTracker " + HOSTILE,
                        category="Coding"),
            WindowEvent(start_time=ago(hours=7), end_time=ago(hours=6, minutes=20),
                        duration_seconds=2400.0, app_name="TeXstudio", window_title="ch3.tex",
                        category="Research"),
        ])
        s.add(DailySummary(date_str=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
                           created_at=ago(days=1), llm_provider="gemini", model_name="lock-model-1",
                           raw_markdown="## 昨天\n\n- 把驗收中心拆成五個檔案 " + HOSTILE + "\n- 補了四支順序回歸測試\n",
                           highlights_json=json.dumps(["驗收中心宣告式化"], ensure_ascii=False),
                           action_items_json=json.dumps(["補前端行為鎖"], ensure_ascii=False)))
        s.add_all([
            SecretaryNote(kind="user_note", project_key="omnicontext", title="收據文化",
                          body="測試綠不等於能用；每一步都要留可驗證的收據。" + HOSTILE, source="web",
                          pinned=True, created_at=ago(days=5)),
            SecretaryNote(kind="preference", title="不要自動找事做",
                          body="減法清單做完就停，下一輪要先決定。", source="web", created_at=ago(days=2)),
            SecretaryNote(kind="observation", project_key="omnicontext",
                          body="這週的 commit 幾乎都集中在 core/ 之下。", source="morning_pack",
                          source_ref="morning_pack:lock", created_at=ago(hours=9)),
        ])
        s.add(SecretaryScheduledTask(template_id="daily_brief", params_json=json.dumps({"window": "today"}),
                                     schedule_kind="daily", run_time="08:30", enabled=True,
                                     last_run_at=ago(hours=10), last_status="succeeded", created_at=ago(days=7)))
        s.add_all([
            GitHubRepoState(repo_name="activityTracker", full_name="dofliu/activityTracker", is_private=False,
                            html_url="https://github.com/dofliu/activityTracker",
                            description="Local-first personal context and activity tracker",
                            default_branch="main", open_prs_count=1, open_issues_count=3,
                            stars_count=12, forks_count=2, pushed_at=ago(minutes=45)),
        ])
        s.add(GitHubPREvent(repo_name="activityTracker", pr_number=40,
                            title="D11＋D12：狀態注入與驗收中心宣告式化 " + HOSTILE, state="open", is_draft=True,
                            author="dofliu", html_url="https://github.com/dofliu/activityTracker/pull/40",
                            branch_head="claude/brave-cerf-wm3f3w", branch_base="main",
                            additions=2927, deletions=1828, changed_files=38,
                            ci_status="success", review_state="PENDING",
                            created_at=ago(days=1), updated_at=ago(hours=1)))
        s.add(BackgroundTaskRun(task_key="lock-bg-1", platform="claude_code", session_id="lock-session",
                                project_tag="omnicontext", cwd="/omni/checkout",
                                started_at=ago(hours=1, minutes=40), completed_at=ago(hours=1),
                                duration_seconds=2400.0, status="completed",
                                start_evidence_kind="user_prompt", completion_evidence_kind="assistant_final",
                                source_path="/omni/transcripts/lock.jsonl", observed_at=ago(hours=1)))
        s.add(CalendarEvent(uid="lock-meeting-1", instance_start=now.replace(hour=15, minute=0, second=0, microsecond=0),
                            instance_end=now.replace(hour=16, minute=0, second=0, microsecond=0),
                            all_day=False, summary="每週進度同步 " + HOSTILE, location="線上",
                            status="CONFIRMED", recurring=True, calendar_name="work",
                            source_path="/omni/calendars/work.ics", last_seen_at=ago(minutes=5)))
        folder = RAGIndexedFolder(path="/omni/docs", name="研究文件 " + HOSTILE, is_active=1,
                                  created_at=ago(days=10), last_scanned_at=ago(hours=4),
                                  file_count=2, total_size=204800)
        s.add(folder)
        s.flush()
        s.add_all([
            RAGIndexedFile(folder_id=folder.id, path="/omni/docs/wind.pdf", filename="wind.pdf",
                           extension=".pdf", file_size=180224, last_modified=1758000000.0,
                           file_hash="a" * 64, chunk_count=42, status="indexed", indexed_at=ago(hours=4)),
            RAGIndexedFile(folder_id=folder.id, path="/omni/docs/notes.md", filename="notes.md",
                           extension=".md", file_size=24576, last_modified=1758000100.0,
                           file_hash="b" * 64, chunk_count=9, status="indexed", indexed_at=ago(hours=4)),
        ])
        s.add(RAGIndexJob(id="11111111-2222-3333-4444-555555555555", job_type="index", folder_id=folder.id,
                          status="completed", requested_at=ago(hours=5), started_at=ago(hours=5),
                          completed_at=ago(hours=4), total_files=2, processed_files=2,
                          indexed_chunks=51, error_count=0, message="索引完成"))
        session = RAGChatSession(id="lock-chat-1", title="離岸風電的容量因數 " + HOSTILE,
                                 created_at=ago(days=1), updated_at=ago(hours=8))
        s.add(session)
        s.add_all([
            RAGChatMessage(session_id="lock-chat-1", role="user", content="容量因數怎麼算？",
                           created_at=ago(hours=9)),
            RAGChatMessage(session_id="lock-chat-1", role="assistant",
                           content="容量因數＝實際發電量 ÷ 額定容量 × 時間。" + HOSTILE,
                           citations=json.dumps([{"path": "/omni/docs/wind.pdf", "page": 12}], ensure_ascii=False),
                           provider="gemini", model="lock-model-1", created_at=ago(hours=8)),
        ])


def redactions(home: Path) -> list[tuple[str, str]]:
    """機器相關的字串換成固定值，快照才不會綁在錄製那台機器上。"""
    pairs = [
        (str(home), HOME_PLACEHOLDER), (home.as_posix(), HOME_PLACEHOLDER),
        (str(REPO_ROOT), REPO_PLACEHOLDER), (REPO_ROOT.as_posix(), REPO_PLACEHOLDER),
        (str(Path.home()), "/omni/user"), (Path.home().as_posix(), "/omni/user"),
    ]
    # 長的先換，免得短的先把長的吃掉一半。
    return sorted({p for p in pairs if p[0]}, key=lambda p: -len(p[0]))


def redact(text: str, pairs: list[tuple[str, str]]) -> str:
    for needle, replacement in pairs:
        text = text.replace(needle, replacement)
    return text


def cmd_record() -> int:
    executable = chromium_path()
    if executable is None:
        print("找不到 Chromium；設 OMNI_CHROMIUM 指到執行檔。", file=sys.stderr)
        return 2

    now = datetime.now().replace(microsecond=0)
    home = Path(tempfile.mkdtemp(prefix="omni-dom-lock-"))
    os.environ["OMNICONTEXT_HOME"] = str(home)
    os.environ.pop("OMNICONTEXT_CONFIG", None)

    try:
        seed_home(home, now)
        from fastapi.testclient import TestClient
        from core.server import app

        client = TestClient(app)
        pairs = redactions(home)
        recorded: dict[str, dict] = {}
        non_get: list[str] = []

        def handler(route, request):
            path_and_query = urlsplit(request.url).path + (
                "?" + urlsplit(request.url).query if urlsplit(request.url).query else ""
            )
            path = urlsplit(request.url).path
            static = static_response(path)
            if static is not None:
                status, ctype, body = static
                route.fulfill(status=status, content_type=ctype, body=body)
                return
            if not path.startswith("/api/"):
                route.fulfill(status=404, content_type="text/plain; charset=utf-8", body="not found")
                return
            if request.method != "GET":
                non_get.append(f"{request.method} {path_and_query}")
                route.fulfill(status=405, content_type="application/json", body='{"detail":"lock: GET only"}')
                return
            res = client.get(path_and_query, headers={"Origin": RECORD_ORIGIN})
            body = redact(res.text, pairs)
            recorded[slug_for(path_and_query)] = {
                "url": path_and_query,
                "status": res.status_code,
                "content_type": res.headers.get("content-type", "application/json"),
                "body": body,
            }
            route.fulfill(status=res.status_code,
                          content_type=res.headers.get("content-type", "application/json"),
                          body=body)

        from playwright.sync_api import sync_playwright

        frozen_ms = int(now.timestamp() * 1000)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=executable)
            for lang in LANGS:
                for tab in TABS:
                    page = new_page(browser, lang, frozen_ms)
                    page.route("**/*", handler)
                    capture_pane(page, tab)
                    page.close()
                for _name, tab, steps in SCENES:
                    page = new_page(browser, lang, frozen_ms)
                    page.route("**/*", handler)
                    capture_pane(page, tab, steps)
                    page.close()
            browser.close()

        if non_get:
            print("錄製期間出現非 GET 的 API 呼叫，這支鎖只錄唯讀開機路徑：", file=sys.stderr)
            for call in sorted(set(non_get)):
                print("   ", call, file=sys.stderr)
            return 2

        if API_DIR.exists():
            shutil.rmtree(API_DIR)
        API_DIR.mkdir(parents=True)
        for name, payload in sorted(recorded.items()):
            (API_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")

        META_PATH.parent.mkdir(parents=True, exist_ok=True)
        META_PATH.write_text(json.dumps({
            "frozen_clock": now.isoformat(),
            "frozen_epoch_ms": frozen_ms,
            "timezone_id": TIMEZONE_ID,
            "locale": LOCALE,
            "viewport": VIEWPORT,
            "langs": list(LANGS),
            "tabs": list(TABS),
            "scenes": [{"name": name, "tab": tab} for name, tab, _steps in SCENES],
            "asset_version": ASSET_VERSION,
            "endpoint_count": len(recorded),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        print(f"錄下 {len(recorded)} 個端點 → {API_DIR.relative_to(REPO_ROOT)}")
        print(f"時鐘凍在 {now.isoformat()}（{TIMEZONE_ID}）")
        print("接著跑 `update` 產生分頁快照。")
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------------------------------------------------------------------------------
# update / check：重播罐頭、拍分頁、比對
# --------------------------------------------------------------------------------------

def load_meta() -> dict:
    if not META_PATH.is_file():
        raise SystemExit(f"沒有 {META_PATH.relative_to(REPO_ROOT)}；先跑 `record`。")
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def load_api_fixtures() -> dict[str, dict]:
    if not API_DIR.is_dir():
        raise SystemExit(f"沒有 {API_DIR.relative_to(REPO_ROOT)}；先跑 `record`。")
    fixtures = {}
    for path in sorted(API_DIR.glob("*.json")):
        fixtures[path.name] = json.loads(path.read_text(encoding="utf-8"))
    if not fixtures:
        raise SystemExit("API 罐頭是空的——這樣跑起來只是空轉。先跑 `record`。")
    return fixtures


def replay(meta: dict, fixtures: dict[str, dict]) -> tuple[dict[str, str], list[str]]:
    executable = chromium_path()
    if executable is None:
        raise SystemExit("找不到 Chromium；設 OMNI_CHROMIUM 指到執行檔。")

    missing: list[str] = []
    panes: dict[str, str] = {}

    def handler(route, request):
        split = urlsplit(request.url)
        path_and_query = split.path + ("?" + split.query if split.query else "")
        static = static_response(split.path)
        if static is not None:
            status, ctype, body = static
            route.fulfill(status=status, content_type=ctype, body=body)
            return
        if not split.path.startswith("/api/"):
            route.fulfill(status=404, content_type="text/plain; charset=utf-8", body="not found")
            return
        fixture = fixtures.get(slug_for(path_and_query))
        if fixture is None:
            missing.append(f"{request.method} {path_and_query}")
            route.fulfill(status=404, content_type="application/json", body='{"detail":"lock: no fixture"}')
            return
        route.fulfill(status=fixture["status"], content_type=fixture["content_type"], body=fixture["body"])

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable)
        for lang in meta["langs"]:
            for tab in meta["tabs"]:
                page = new_page(browser, lang, meta["frozen_epoch_ms"])
                page.route("**/*", handler)
                panes[f"{tab}.{lang}"] = capture_pane(page, tab)
                page.close()
            for name, tab, steps in SCENES:
                page = new_page(browser, lang, meta["frozen_epoch_ms"])
                page.route("**/*", handler)
                panes[f"{name}.{lang}"] = capture_pane(page, tab, steps)
                page.close()
        browser.close()
    return panes, missing


def pane_path(key: str) -> Path:
    return PANE_DIR / f"{key}.html"


def first_difference(old: str, new: str) -> str:
    limit = min(len(old), len(new))
    index = next((i for i in range(limit) if old[i] != new[i]), limit)
    start = max(0, index - 120)
    return (f"      第 {index} 個字元起不同\n"
            f"      舊：…{old[start:index + 120]!r}\n"
            f"      新：…{new[start:index + 120]!r}")


def cmd_update() -> int:
    meta = load_meta()
    panes, missing = replay(meta, load_api_fixtures())
    if missing:
        print("重播時有 API 沒有罐頭，快照會失真：", file=sys.stderr)
        for call in sorted(set(missing)):
            print("   ", call, file=sys.stderr)
        return 2
    if PANE_DIR.exists():
        shutil.rmtree(PANE_DIR)
    PANE_DIR.mkdir(parents=True)
    for key, html in sorted(panes.items()):
        pane_path(key).write_text(html, encoding="utf-8")
    print(f"寫下 {len(panes)} 張快照 → {PANE_DIR.relative_to(REPO_ROOT)}")
    return 0


def cmd_check() -> int:
    meta = load_meta()
    expected_keys = sorted(
        [f"{tab}.{lang}" for tab in meta["tabs"] for lang in meta["langs"]]
        + [f"{scene['name']}.{lang}" for scene in meta.get("scenes", []) for lang in meta["langs"]]
    )
    stored = sorted(p.name[: -len(".html")] for p in PANE_DIR.glob("*.html")) if PANE_DIR.is_dir() else []
    if stored != expected_keys:
        print(f"快照檔不齊：預期 {expected_keys}，實際 {stored}。先跑 `update`。", file=sys.stderr)
        return 2

    panes, missing = replay(meta, load_api_fixtures())
    problems = 0
    if missing:
        problems += 1
        print("重播時有 API 沒有罐頭：", file=sys.stderr)
        for call in sorted(set(missing)):
            print("   ", call, file=sys.stderr)

    for key in expected_keys:
        old = pane_path(key).read_text(encoding="utf-8")
        new = panes[key]
        if old == new:
            print(f"  = {key}  ({len(old)} 字元)")
            continue
        problems += 1
        print(f"  ≠ {key}  舊 {len(old)} 字元 / 新 {len(new)} 字元")
        print(first_difference(old, new))

    if problems:
        print(f"\n前端行為鎖：{problems} 處不同。", file=sys.stderr)
        return 1
    print(f"\n前端行為鎖：{len(expected_keys)} 張快照逐字元相同。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("record", "update", "check"))
    args = parser.parse_args()
    return {"record": cmd_record, "update": cmd_update, "check": cmd_check}[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
