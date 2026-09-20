"""前端共享狀態的契約：十一個具名 store、欄位有歸屬、可以另外造一份（ADR-030，TODO D13）。

D10 把四十九個散落的模組層 `let` 收進一個具名物件（ADR-026），但四十九個欄位全部攤平在
同一層。攤平的代價有兩個，都不會拋錯：

1. **打錯字是靜默的**。`state.homeCahe` 讀到 `undefined`，畫面就少一塊，沒有任何訊息。
2. **沒有歸屬**。`streamAbort` 是誰的？誰會寫 `homeCache`？沒有東西回答得了。

D13 把它們分成十一個 store，於是上面兩件事都可以用測試問：每一個 `state.<store>.<field>`
都必須真的存在（打錯字會紅），而「哪個模組碰哪個 store」是下面那張表——**新的跨模組存取
會讓測試紅，必須先寫進表裡，也就是先講出來**。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "web" / "js"
STATE_JS = JS / "core" / "state.js"
MODULES = sorted(p for p in JS.rglob("*.js") if p.name != "state.js")

# 哪個模組碰哪個 store。這張表是**宣告**，不是觀察報告：要新增一條就是承認多了一處跨模組存取。
OWNERSHIP = {
    "ui": ["assistant.js", "health.js", "i18n.js", "knowledge.js", "memory.js",
           "projects.js", "repos.js", "settings.js", "status.js"],
    "feed": ["projects.js", "status.js"],
    "projects": ["assistant.js", "i18n.js", "projects.js", "status.js", "summaries.js"],
    "focus": ["projects.js"],
    "summaries": ["summaries.js"],
    # i18n 會讀四個 store 的快取，決定切語言時要重畫哪些區塊。D13 之前這件事看不出來。
    "secretary": ["assistant.js", "i18n.js", "projects.js", "settings.js"],
    "memory": ["i18n.js", "memory.js"],
    "rag": ["assistant.js", "knowledge.js", "memory.js"],
    "repos": ["repos.js"],
    "settings": ["github.js", "settings.js"],
    "health": ["health.js", "i18n.js"],
}


def stores() -> dict[str, list[str]]:
    """把 `createAppState()` 的形狀讀出來。檔案格式變了就會讀不到，測試會因為數量不足而紅。"""
    source = STATE_JS.read_text(encoding="utf-8")
    # 用完整的宣告當界標：檔頭註解裡也有「export const state」這幾個字。
    body = source[source.index("export function createAppState()"):
                  source.index("export const state = createAppState();")]
    found: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        opening = re.match(r"^    (\w+): \{$", line)
        if opening:
            current = opening.group(1)
            found[current] = []
            continue
        if line == "    },":
            current = None
            continue
        field = re.match(r"^      (\w+):", line)
        if field and current:
            found[current].append(field.group(1))
    return found


def used_paths() -> set[tuple[str, str, str]]:
    """所有模組裡出現過的 (檔名, store, field)。"""
    seen = set()
    for path in MODULES:
        for match in re.finditer(r"(?<![.\w])state\.([a-z]\w*)\.(\w+)", path.read_text(encoding="utf-8")):
            seen.add((path.name, match.group(1), match.group(2)))
    return seen


def test_state_js_declares_eleven_stores_and_all_49_fields():
    found = stores()
    assert len(found) == 11, f"預期 11 個 store，讀到 {sorted(found)}"
    total = sum(len(v) for v in found.values())
    assert total == 49, f"預期 49 個欄位，讀到 {total}——D10 收進來的就是這 49 個"


def test_every_state_path_used_by_a_module_really_exists():
    """打錯 store 或欄位名在瀏覽器裡是靜默的 undefined；在這裡是紅字。"""
    declared = stores()
    paths = used_paths()
    assert len(paths) >= 40, f"只掃到 {len(paths)} 條路徑——這個測試會變成空轉"
    unknown = sorted(f"{name}: state.{store}.{field}" for name, store, field in paths
                     if store not in declared or field not in declared[store])
    assert unknown == [], f"這些路徑在 state.js 裡不存在：{unknown}"


def test_no_module_reaches_a_flat_field_any_more():
    """`state.<欄位>` 這種攤平的存取必須絕跡；剩下的只能是 `state.<store>.<欄位>`。"""
    declared = stores()
    flat = []
    for path in MODULES:
        text = path.read_text(encoding="utf-8")
        # 先把整條路徑抓完再看深度。用 lookahead 擋「後面不是點」會被回溯騙過去
        # （`state.secretary.` 會被拆成 `state.secretar` ＋ 一個 `y`），第一版就是這樣誤報的。
        for match in re.finditer(r"(?<![.\w])state\.(\w+)(\.\w+)?", text):
            if match.group(2):
                continue
            name = match.group(1)
            if name in declared:      # state.rag 這種只寫到 store 為止的用法（目前沒有，但合法）
                continue
            if name == "js":          # import "../core/state.js"
                continue
            line = text[:match.start()].count("\n") + 1
            flat.append(f"{path.name}:{line} state.{name}")
    assert flat == [], f"這些地方還在用攤平的欄位：{flat}"


def test_who_touches_which_store_matches_the_declared_table():
    """跨模組存取不是禁止，是**必須講出來**。多一處沒宣告的就紅。"""
    actual: dict[str, set[str]] = {}
    for name, store, _field in used_paths():
        actual.setdefault(store, set()).add(name)
    assert sorted(actual) == sorted(OWNERSHIP), f"store 集合對不上：{sorted(actual)}"
    drift = {store: sorted(actual[store] ^ set(OWNERSHIP[store]))
             for store in actual if actual[store] != set(OWNERSHIP[store])}
    assert drift == {}, f"和宣告的歸屬表不一致（多出來或少掉的模組）：{drift}"


def test_no_store_is_empty():
    empty = [name for name, fields in stores().items() if not fields]
    assert empty == [], f"空的 store 沒有意義：{empty}"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能真的把模組載進來跑")
def test_create_app_state_really_makes_independent_copies():
    """D13 之前造不出第二份狀態（`export const state = {…}` 是唯一一份）。這裡證明現在可以。"""
    script = """
    globalThis.localStorage = { getItem: () => null, setItem: () => {} };
    const m = await import(process.argv[1]);
    const a = m.createAppState(), b = m.createAppState();
    a.rag.history.push("x");
    a.projects.expandedKey = "p";
    a.repos.overviewBatch.push = true;
    console.log(JSON.stringify({
      stores: Object.keys(a).length,
      fields: Object.values(a).reduce((n, v) => n + Object.keys(v).length, 0),
      independent: b.rag.history.length === 0 && b.projects.expandedKey === null
                   && b.repos.overviewBatch.push === false,
      default_untouched: m.state.rag.history.length === 0,
    }));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(STATE_JS)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {"stores": 11, "fields": 49, "independent": True, "default_untouched": True}, report
