"""守著前端行為鎖的語料本身（ADR-029，TODO D11 續）。

真正的比對要開瀏覽器，那是 `scripts/dashboard_dom_lock.py check` 的事，預設不在 pytest 裡跑
（十二次開機，約一分鐘；設 `OMNI_DOM_LOCK=1` 就會跑本檔最後那一支）。

**但是「語料本身有沒有資格當鎖」不需要瀏覽器就能問**，而且這正是最容易爛掉的地方：
拿一個空資料庫重錄一次，十二張快照會全部變成「沒有資料」的空殼，`check` 依然全綠——
鎖還在，但它什麼都不鎖了。下面這些測試就是擋這件事的：快照要夠大、要真的含種子資料、
要含跳脫過的惡意字串、分頁矩陣要跟 `index.html` 對得上、不准夾帶某台機器的路徑。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "dashboard_dom"
API_DIR = FIXTURE_DIR / "api"
PANE_DIR = FIXTURE_DIR / "panes"
META_PATH = FIXTURE_DIR / "meta.json"
LOCK_SCRIPT = REPO_ROOT / "scripts" / "dashboard_dom_lock.py"

# 種子裡那串會咬人的字串（scripts/dashboard_dom_lock.py 的 HOSTILE）在畫面上應有的樣子。
ESCAPED_MARKERS = ("&lt;b&gt;粗體&lt;/b&gt;", "&lt;img src=x onerror=1&gt;")
RAW_INJECTION = "<img src=x onerror=1>"

# 種子資料在畫面上留下的痕跡：空資料庫重錄的話這些會一起消失。
SEED_TRACES = {
    "tab-assistant": ("收據文化",),
    "tab-knowledge": ("研究文件", "容量因數"),
    # 痕跡要挑「兩種語言都一樣」的資料值，不要挑會被字典翻掉的介面字。
    "tab-projects": ("thesis-wind", "一個 reading 加一張階梯表", "PR #40"),
    "tab-summaries": ("把驗收中心拆成五個檔案",),
}


def meta() -> dict:
    assert META_PATH.is_file(), "沒有 meta.json——前端行為鎖的語料不完整"
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def pane_files() -> list[Path]:
    return sorted(PANE_DIR.glob("*.html"))


def test_meta_matches_the_panes_on_disk():
    data = meta()
    expected = sorted(
        [f"{tab}.{lang}.html" for tab in data["tabs"] for lang in data["langs"]]
        + [f"{scene['name']}.{lang}.html" for scene in data["scenes"] for lang in data["langs"]]
    )
    assert [p.name for p in pane_files()] == expected
    assert len(expected) == 22, f"預期 (6 分頁 ＋ 5 場景) × 2 語言，實際 {len(expected)}"


def test_every_scene_actually_changed_something():
    """場景快照和它那一頁的第一畫面必須不同。

    一樣就代表那個互動什麼都沒做——選擇器失效、按鈕改名、面板被移走都會長這樣，而且
    `check` 依然全綠（兩邊都退化成同一張第一畫面）。這是場景最容易爛掉的方式。
    """
    data = meta()
    idle = []
    for scene in data["scenes"]:
        for lang in data["langs"]:
            after = (PANE_DIR / f"{scene['name']}.{lang}.html").read_text(encoding="utf-8")
            before = (PANE_DIR / f"{scene['tab']}.{lang}.html").read_text(encoding="utf-8")
            if after == before:
                idle.append(f"{scene['name']}.{lang}")
    assert idle == [], f"這些場景和第一畫面一模一樣，等於沒做互動：{idle}"


def test_scenes_cover_the_shared_values_the_refactor_will_touch():
    """場景挑的不是隨便五個動作，而是五組會讀寫共享值的動作。少一組就是少一份證據。"""
    assert sorted(s["name"] for s in meta()["scenes"]) == [
        "knowledge-session",    # currentRagSessionId / ragChatHistory
        "projects-expand",      # expandedProject / showAllProjects / projectsCache
        "settings-feed-git",    # activeFilter / recentEvents
        "settings-pane-llm",    # currentConfig
        "summaries-week",       # summaryView / summariesCache
    ]


def test_meta_pins_everything_that_would_otherwise_drift():
    data = meta()
    for key in ("frozen_clock", "frozen_epoch_ms", "timezone_id", "locale", "viewport", "asset_version"):
        assert data.get(key), f"meta.json 少了 {key}——少一項就等於少釘一個會飄的東西"
    assert data["frozen_epoch_ms"] > 0


def test_pane_matrix_covers_every_tab_in_index_html():
    """加了第七個分頁卻沒擴充鎖，這裡會紅——鎖不會默默地少守一頁。"""
    html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    in_page = sorted(set(re.findall(r'data-tab="([^"]+)"', html)))
    assert in_page, "index.html 掃不到任何 data-tab——這個測試會變成空轉"
    assert in_page == sorted(meta()["tabs"])


def test_every_pane_snapshot_is_substantial():
    """空殼快照也能「逐字元相同」。夠大不等於夠好，但太小一定是壞了。"""
    thin = [(p.name, len(p.read_text(encoding="utf-8"))) for p in pane_files()
            if len(p.read_text(encoding="utf-8")) < 1500]
    assert thin == [], f"這些快照薄到不像有渲染過：{thin}"


def test_panes_carry_seeded_data_not_just_empty_states():
    """拿空資料庫重錄一次，鎖還是全綠但什麼都不鎖了。這支測試就是擋那件事。"""
    missing = []
    for tab, traces in SEED_TRACES.items():
        for lang in meta()["langs"]:
            text = (PANE_DIR / f"{tab}.{lang}.html").read_text(encoding="utf-8")
            missing += [f"{tab}.{lang} 少了 {trace!r}" for trace in traces if trace not in text]
    assert missing == [], f"快照裡看不到種子資料，鎖住的可能只是空狀態：{missing}"


def test_panes_show_user_content_escaped_and_never_raw():
    """種子刻意塞了 `"` `<` `&`：跳脫壞掉的話，這裡會在沒有瀏覽器的情況下就紅。

    第一版種子全是乖巧的中文，結果把 `esc()` 的 `&quot;` 拿掉，十二張快照一個字元都沒變。
    現在每一條渲染通道都有帶引號與角括號的資料，所以這件事在語料層就守得住。
    """
    escaped_hits = 0
    for path in pane_files():
        text = path.read_text(encoding="utf-8")
        assert RAW_INJECTION not in text, f"{path.name} 裡有沒跳脫的 HTML 注入"
        escaped_hits += sum(text.count(marker) for marker in ESCAPED_MARKERS)
    assert escaped_hits >= 20, f"跳脫過的惡意字串只出現 {escaped_hits} 次，覆蓋太薄"


def test_quote_escaping_is_visible_in_the_corpus():
    """`&quot;` 至少要在幾張快照裡看得到，否則拿掉引號跳脫不會被任何東西擋下。"""
    with_quotes = [p.name for p in pane_files() if "&quot;" in p.read_text(encoding="utf-8")]
    assert len(with_quotes) >= 4, f"只有 {with_quotes} 含 &quot;——引號跳脫等於沒鎖"


def test_api_fixtures_are_complete_and_parse():
    files = sorted(API_DIR.glob("*.json"))
    assert len(files) >= 35, f"API 罐頭只有 {len(files)} 個，開機與互動問的端點不只這些"
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in ("url", "status", "content_type", "body"):
            assert key in payload, f"{path.name} 少了 {key}"
        assert payload["url"].startswith("/api/"), path.name
        assert payload["body"], f"{path.name} 的 body 是空的"


def test_fixtures_carry_no_machine_specific_paths():
    """錄製時的臨時 home 與 checkout 路徑要被換掉，語料才不綁在某一台機器上。"""
    forbidden = ("/home/user", "/tmp/omni-dom-lock-", "/root/", "C:\\\\Users")
    offenders = []
    for path in sorted(API_DIR.glob("*.json")) + pane_files():
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {needle}" for needle in forbidden if needle in text]
    assert offenders == [], f"語料裡夾帶了機器相關的路徑：{offenders}"


def test_lock_script_imports_nothing_heavy_at_module_level():
    """playwright 與 core 都是延遲匯入：沒裝 playwright 的機器也要能 import 這支腳本。"""
    source = LOCK_SCRIPT.read_text(encoding="utf-8")
    module_level = [line for line in source.splitlines()
                    if re.match(r"^(import|from)\s+", line)]
    banned = [line for line in module_level
              if re.search(r"^\s*(import|from)\s+(playwright|core|fastapi)\b", line)]
    assert banned == [], f"這些應該延遲匯入：{banned}"


@pytest.mark.skipif(os.environ.get("OMNI_DOM_LOCK") != "1",
                    reason="真正的比對要開十二次瀏覽器（約一分鐘）；設 OMNI_DOM_LOCK=1 才跑")
def test_dom_lock_check_is_green():
    result = subprocess.run([sys.executable, str(LOCK_SCRIPT), "check"],
                            cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800)
    assert result.returncode == 0, result.stdout + result.stderr
