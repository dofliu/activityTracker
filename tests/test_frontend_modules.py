"""前端模組化的契約（ADR-026，TODO D10）。

D10 之前 `web/app.js` 是 6,096 行的單一檔案：六個分頁、一份 682 行的字典、49 個模組層
`let`、九處繞過共用 helper 的裸 `fetch()`、17 處寫在 HTML 字串裡的 `onclick=`。

這裡把「拆完之後不准退回去」寫成測試：

1. 沒有單檔 > 1,500 行。
2. `fetch(` 只准出現在 `core/api.js`。
3. `onclick=` 一處都不准有（它要求函式掛在 `window` 上，模組化就是被這個卡住的）。
4. 每個 `data-action="X"` 都要有人 `registerActions` 註冊——否則按了沒反應而且不會報錯。
5. **兩份語系字典的 key 集合必須完全相同**。這是 D10 之前唯一沒人守的地方：少一個 key
   不會拋錯，它會安靜地 fallback，英文介面就這樣夾一句中文。
6. `index.html` 用到的每個 `data-i18n` key 都要在兩份字典裡。
7. 共享可變狀態只准住在 `core/state.js`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
JS = WEB / "js"
MAX_LINES = 1500

JS_FILES = sorted(JS.rglob("*.js"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- 1. 檔案大小 -----------------------------------------------------------


def test_the_single_file_frontend_is_gone():
    assert not (WEB / "app.js").exists(), "web/app.js 應該已經拆掉"
    assert (JS / "main.js").exists() and JS_FILES, "web/js/ 應該有模組"


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.relative_to(JS).as_posix())
def test_no_frontend_file_is_over_1500_lines(path):
    lines = len(_text(path).splitlines())
    assert lines <= MAX_LINES, f"{path.relative_to(WEB)} 有 {lines} 行"


# ---- 2./3. fetch 與 onclick ------------------------------------------------


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.relative_to(JS).as_posix())
def test_only_the_api_module_calls_fetch(path):
    """狀態碼檢查與後端 detail 取值只寫一次（ADR-026）。"""
    if path.relative_to(JS).as_posix() == "core/api.js":
        return
    assert "fetch(" not in _text(path), f"{path.relative_to(WEB)} 自己呼叫了 fetch"


def test_no_inline_onclick_anywhere():
    """`onclick=` 要求函式掛在 window 上，而且參數是拼進 HTML 字串的。"""
    offenders = [p.relative_to(ROOT).as_posix() for p in [*JS_FILES, WEB / "index.html"]
                 if "onclick=" in _text(p)]
    assert offenders == [], offenders


def test_no_module_assigns_to_window():
    """模組化之後不該再需要全域命名空間。"""
    offenders = [f"{p.relative_to(WEB)}:{i}" for p in JS_FILES
                 for i, line in enumerate(_text(p).splitlines(), 1)
                 if re.match(r"^\s*window\.\w+\s*=", line)]
    assert offenders == [], offenders


# ---- 4. 事件委派 -----------------------------------------------------------


def _registered_actions() -> set[str]:
    names: set[str] = set()
    for path in JS_FILES:
        for block in re.findall(r"registerActions\(\{(.*?)\n\s*\}\);", _text(path), re.S):
            names.update(re.findall(r'"([\w-]+)"\s*:', block))
    return names


def _used_actions() -> set[str]:
    names: set[str] = set()
    for path in JS_FILES:
        names.update(re.findall(r'data-action="([\w-]+)"', _text(path)))
    return names


def test_every_data_action_has_a_registered_handler():
    used, registered = _used_actions(), _registered_actions()
    assert used, "應該有 data-action 按鈕"
    assert used - registered == set(), f"沒有 handler：{sorted(used - registered)}"
    assert registered - used == set(), f"註冊了但沒人用：{sorted(registered - used)}"


def test_the_delegation_helper_lives_in_one_place():
    ui = _text(JS / "core" / "ui.js")
    assert "export function initActionDelegation()" in ui
    assert "export function registerActions(" in ui


# ---- 5./6. 語系字典 --------------------------------------------------------


def _dictionaries() -> dict[str, dict]:
    return {lang: json.loads((WEB / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("zh-TW", "en")}


def test_both_dictionaries_carry_exactly_the_same_keys():
    """D10 之前唯一沒人守的地方：少一個 key 不會報錯，它會安靜地 fallback。"""
    dicts = _dictionaries()
    zh, en = set(dicts["zh-TW"]), set(dicts["en"])
    assert zh - en == set(), f"只有 zh-TW 有：{sorted(zh - en)}"
    assert en - zh == set(), f"只有 en 有：{sorted(en - zh)}"
    assert len(zh) > 300


def test_no_dictionary_value_is_empty():
    for lang, dictionary in _dictionaries().items():
        blank = [k for k, v in dictionary.items() if not str(v).strip()]
        assert blank == [], f"{lang} 有空字串：{blank}"


def test_every_i18n_key_used_by_index_html_exists_in_both_dictionaries():
    html = _text(WEB / "index.html")
    used = set(re.findall(r'data-i18n(?:-ph)?="([\w-]+)"', html))
    dicts = _dictionaries()
    for lang, dictionary in dicts.items():
        missing = sorted(used - set(dictionary))
        assert missing == [], f"{lang} 少了 index.html 用到的 key：{missing}"


def test_dictionaries_are_data_not_code():
    """字典不得再出現在程式裡（D10 之前是 app.js 的一個 682 行物件）。"""
    for path in JS_FILES:
        text = _text(path)
        assert "lang_btn:" not in text and '"lang_btn":' not in text, path.relative_to(WEB)
    assert "loadDictionaries" in _text(JS / "core" / "i18n.js")


# ---- 7. 共享狀態 -----------------------------------------------------------


def test_shared_mutable_state_lives_only_in_state_js():
    """模組層 `let` 一旦跨檔就不能再被重新賦值；D10 把它們收進一個具名物件。

    這裡只保證它們沒有再散回各個模組。分成具名 store 與工廠是 D13 的事（ADR-030），
    那部分的契約在 `tests/test_frontend_state_stores.py`。
    """
    offenders = [f"{p.relative_to(WEB)}:{i}" for p in JS_FILES
                 if p.name != "state.js"
                 for i, line in enumerate(_text(p).splitlines(), 1)
                 if re.match(r"^let\s+\w+", line)]
    assert offenders == [], offenders
    state = _text(JS / "core" / "state.js")
    assert "export function createAppState()" in state
    assert "export const state = createAppState();" in state


def test_nothing_shadows_the_shared_state():
    """匯入了共享 `state` 的檔案裡，不准再有叫 `state` 的參數或區域變數。

    這不是潔癖。`status.js` 的 `captureStateLabel(state)` 就是這樣壞掉的：參數是一個字串
    （"observed" 之類），把匯入的共享 `state` 遮蔽掉，於是 `state.currentLang` 讀的是字串的
    `.currentLang`——永遠 undefined，於是**中文介面一直拿到英文標籤**。不會拋錯、不會有
    console 訊息、review 也看不出來，因為那一行單獨看完全正常。同一個檔案裡的
    `renderCaptureCoverage` 用的就是共享的 `state.currentLang`，可見是不小心遮到。

    這是「一個叫 state 的共享物件」這種設計會招來的錯，所以把整個類別擋掉。
    """
    imports_state = re.compile(r'from "(?:\.\./)?(?:\./)?core/state\.js"|from "\./state\.js"')
    param = re.compile(r"\bfunction\s+\w+\s*\([^)]*\bstate\b")
    local = re.compile(r"\b(?:const|let|var)\s+state\b")
    arrow = re.compile(r"\(\s*state\s*\)\s*=>")
    checked = 0
    offenders = []
    for path in JS_FILES:
        if path.name == "state.js":
            continue
        text = _text(path)
        if not imports_state.search(text):
            continue
        checked += 1
        for number, line in enumerate(text.splitlines(), 1):
            if param.search(line) or local.search(line) or arrow.search(line):
                offenders.append(f"{path.relative_to(WEB)}:{number} {line.strip()[:70]}")
    assert checked >= 10, f"只掃到 {checked} 個檔案——這個測試會變成空轉"
    assert offenders == [], f"這些地方會遮蔽共享的 state：{offenders}"


def test_index_html_loads_the_module_entry_point():
    html = _text(WEB / "index.html")
    assert '<script type="module" src="/static/js/main.js?v=__ASSET_VERSION__"></script>' in html
    assert "/static/app.js" not in html
