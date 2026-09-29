"""E6：`omni init --detect` 偵測本機逐字稿來源——**偵測不等於啟用**。

TODO E6 的完成判準逐條落成測試：偵測結果與實際存在的路徑一致、一律要明確回答才寫、
預設不納入、`--yes` 之類的非互動旗標**不存在**、偵測不等於啟用。
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
from pathlib import Path

import pytest
import yaml

import main as main_module
from core import source_detection
from core.source_detection import PATH_KEYS, Candidate, describe, detect_sources

ROOT = Path(__file__).resolve().parents[1]

# 危險能力與採集開關——**偵測這條路徑一個都不准碰**。
UNTOUCHABLE_KEYS = (
    "watchers.agent_log_watcher.enabled",
    "watchers.agent_log_watcher.claude_code",
    "watchers.agent_log_watcher.claude_desktop",
    "watchers.agent_log_watcher.codex",
    "watchers.agent_log_watcher.antigravity",
    "proactive_secretary.executor.enabled",
    "proactive_secretary.executor.l2_enabled",
    "proactive_secretary.executor.l2_write_enabled",
    "security.browser_extension_ingest_token",
    "security.execution_token",
    "mcp.enabled",
)


class _Cfg:
    """最小的 TranscriptConfig：只有 get／get_path。"""

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key_path, default=None):
        node = self.data
        for part in key_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def get_path(self, key_path, default=""):
        return Path(str(self.get(key_path, default) or "")).expanduser()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """一個只有 Claude Code 與 Codex 的假家目錄——另外兩個平台刻意不存在。"""
    home = tmp_path / "home"
    claude = home / ".claude" / "projects" / "demo"
    claude.mkdir(parents=True)
    (claude / "a.jsonl").write_text('{"type": "user"}\n', encoding="utf-8")
    (claude / "b.jsonl").write_text('{"type": "user"}\n', encoding="utf-8")
    codex = home / ".codex" / "sessions"
    codex.mkdir(parents=True)
    (codex / "rollout.jsonl").write_text('{"type": "session_meta"}\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # `Path.home()` 不夠：設定檔裡寫的是 `~/.claude`，而 `expanduser()` 走的是
    # HOME／USERPROFILE 環境變數，不是 `Path.home`。2026-09-29 修 B8 時這個洞才現形——
    # 在那之前範本的 `claude_code_logs_path` 根本讀不到，所以永遠走 `default_logs_dir()`
    # 的 `Path.home()` 分支，漏掉的那一半沒有機會出事。補上之後這條測試才真的只看假家目錄，
    # 而不是讀到跑測試那台機器上真正的 `~/.claude`。
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


# ---- 偵測：只看不寫 -------------------------------------------------------------


def test_detection_lists_only_paths_that_actually_exist(fake_home):
    found = {c.key: c for c in detect_sources(_Cfg())}
    assert set(found) == {"claude_code", "codex"}, "不存在的平台被列出來了"
    assert found["claude_code"].transcripts == 2
    assert found["codex"].transcripts == 1
    for candidate in found.values():
        assert candidate.path.is_dir(), "列出來的路徑點不開"


def test_detection_lists_an_existing_directory_that_has_no_transcripts_yet(fake_home):
    """「目錄在但還沒有逐字稿」跟「沒有這個平台」是兩件事，不要混為一談。"""
    (fake_home / ".gemini" / "antigravity" / "brain").mkdir(parents=True)
    found = {c.key: c for c in detect_sources(_Cfg())}
    assert "antigravity" in found
    assert found["antigravity"].transcripts == 0
    assert "還沒有逐字稿" in describe(found["antigravity"])


def test_detection_writes_nothing(fake_home, tmp_path):
    """偵測是可以隨便跑幾次的動作——所以它一個位元組都不准寫。"""
    before = sorted((p, p.stat().st_mtime_ns) for p in fake_home.rglob("*"))
    detect_sources(_Cfg())
    detect_sources(_Cfg())
    assert sorted((p, p.stat().st_mtime_ns) for p in fake_home.rglob("*")) == before


def test_detection_marks_paths_that_are_already_in_the_config(fake_home):
    cfg = _Cfg({"watchers": {"agent_log_watcher": {
        "claude_code_logs_path": str(fake_home / ".claude")}}})
    found = {c.key: c for c in detect_sources(cfg)}
    assert found["claude_code"].already_configured is True
    assert found["codex"].already_configured is False
    assert "已經有這個路徑" in describe(found["claude_code"])


def test_detection_reuses_the_package_discover_instead_of_a_second_glob():
    """自己寫一套 glob 就會跟 parser 漂移（Claude Code 有 history.jsonl 備援、
    Desktop 埋在四層底下、Codex 認兩種副檔名）。那些規則已經有一份了。"""
    tree = ast.parse((ROOT / "core" / "source_detection.py").read_text(encoding="utf-8"))
    globs = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"glob", "rglob", "walk"}
    ]
    assert globs == [], f"偵測不得自己寫 glob（第 {globs} 行）"
    source = (ROOT / "core" / "source_detection.py").read_text(encoding="utf-8")
    assert "source.discover" in source, "偵測應該用平台自己的 discover()"


# ---- 偵測不等於啟用 -------------------------------------------------------------


def test_only_path_keys_can_ever_be_written():
    """寫得進去的鍵就這四個。開關類的一個都不在名單上。"""
    assert set(PATH_KEYS) == {"claude_code", "claude_desktop", "codex", "antigravity"}
    for key in PATH_KEYS.values():
        assert key.endswith("_logs_path"), key
    for banned in UNTOUCHABLE_KEYS:
        assert banned not in PATH_KEYS.values()


def test_the_detection_module_only_ever_names_path_keys():
    """掃原始碼：這一層提到的每一個「設定鍵字面值」都必須是路徑鍵。

    **第一版寫成「掃鍵名的最後一段」，當場自爆**：`watchers.agent_log_watcher.claude_code`
    的最後一段是 `claude_code`，而那正是 `PATH_KEYS` 的字典鍵（平台代號）。
    這是 ADR-032 D1 記過的同一個坑——子字串掃描會被自己的合法內容絆倒。
    正確的形狀是 AST 取出所有字串常數，只挑**長得像 dotted 設定鍵**的那些來比對。
    """
    tree = ast.parse((ROOT / "core" / "source_detection.py").read_text(encoding="utf-8"))
    dotted = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.count(".") >= 2 and " " not in node.value
    }
    assert dotted, "掃不到任何設定鍵字面值，這條會變成空轉"
    assert dotted <= set(PATH_KEYS.values()), (
        f"偵測這一層提到了路徑以外的設定鍵：{sorted(dotted - set(PATH_KEYS.values()))}"
    )
    for banned in UNTOUCHABLE_KEYS:
        assert banned not in dotted, banned


def test_accepting_a_source_writes_the_path_and_nothing_else(fake_home, monkeypatch):
    """**這是 E6 最核心的一條**：答應納入，動到的只有路徑。"""
    config_data = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    before = copy.deepcopy(config_data)

    monkeypatch.setattr(main_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    written = main_module._detect_and_ask(config_data)

    # 假家目錄裡有兩個來源，但範本**本來就寫著** `claude_code_logs_path: ~/.claude`，
    # 所以只有 Codex 是待問的。2026-09-29 修 B8 之前這裡是 2——那時候 `watchers:` 底下
    # 讀不到那個鍵，於是一個明明已經設好的平台每次都被重問一次。**這一條的數字從 2 變 1，
    # 量的正是那個缺陷的修復**：`already_configured` 現在真的認得出範本設過的東西。
    assert written == 1, "只有 Codex 是待問的（Claude Code 範本已設）"

    node = config_data["watchers"]["agent_log_watcher"]
    assert node["claude_code_logs_path"] == "~/.claude", "已經設好的鍵被覆寫了"
    assert node["codex_logs_path"] == str(fake_home / ".codex")

    # 除了那個新寫入的路徑鍵，設定檔逐鍵相同。
    after = copy.deepcopy(config_data)
    after["watchers"]["agent_log_watcher"].pop("codex_logs_path")
    assert json.dumps(after, sort_keys=True, default=str) == json.dumps(
        before, sort_keys=True, default=str
    ), "偵測動到了路徑以外的東西"


@pytest.mark.parametrize("answer", ["", " ", "n", "N", "no", "maybe", "Y E S", "1", "true"])
def test_anything_that_is_not_yes_means_no(fake_home, monkeypatch, answer):
    """預設是不納入。**只有明確的 y／yes 算同意**——`true`／`1` 都不算。"""
    config_data = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    before = copy.deepcopy(config_data)
    monkeypatch.setattr(main_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert main_module._detect_and_ask(config_data) == 0
    assert config_data == before


def test_a_non_interactive_run_writes_nothing_at_all(fake_home, monkeypatch, capsys):
    """沒有人在那頭回答，任何「寫進去」都等於替使用者決定。"""
    config_data = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    before = copy.deepcopy(config_data)
    monkeypatch.setattr(main_module.sys.stdin, "isatty", lambda: False)

    def _must_not_be_called(_prompt):  # pragma: no cover - 命中就是壞了
        raise AssertionError("非互動環境不得呼叫 input()")

    monkeypatch.setattr("builtins.input", _must_not_be_called)
    assert main_module._detect_and_ask(config_data) == 0
    assert config_data == before
    assert "沒有寫入任何設定" in capsys.readouterr().out


def test_there_is_no_auto_accept_flag(fake_home):
    """**不是「預設關閉」，是沒有這個東西。**

    一個 `--yes`／`--all`／`--non-interactive` 旗標會讓「一定要明確回答」變成裝飾品：
    寫腳本的人會加上它，然後所有同意權就消失了。所以它不存在，而這條測試守著它不出現。
    """
    parser = main_module.build_parser() if hasattr(main_module, "build_parser") else None
    if parser is None:
        # main.py 的 parser 建在 main() 裡，改掃 argparse 的宣告本身。
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        flags = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
    else:  # pragma: no cover - 目前沒有 build_parser
        flags = set()
    banned = {"--yes", "-y", "--all", "--non-interactive", "--assume-yes", "--force-detect", "--auto"}
    assert not (flags & banned), f"出現了自動同意旗標：{sorted(flags & banned)}"
    assert "--detect" in flags, "掃不到 --detect，這條測試會變成空轉"


# ---- 套件那一側：四個平台都要說得出預設位置 ----------------------------------------


def test_every_platform_exposes_a_default_location():
    from coding_agent_transcripts import SOURCES

    for source in SOURCES:
        assert source.default_logs_dir is not None, f"{source.key} 沒有預設位置"
        assert isinstance(source.default_logs_dir(), Path)
    assert set(PATH_KEYS) == {s.key for s in SOURCES}, "偵測的鍵表與平台清單漂移了"


def test_a_default_location_is_a_candidate_not_a_collection_target(fake_home):
    """Antigravity 沒設定時 `discover()` 回空清單——**它不會自己拿預設位置去採集**。

    猜一個位置去採集，跟提議一個位置請使用者確認，差別就是同意權在誰手上。
    """
    from coding_agent_transcripts import antigravity

    (fake_home / ".gemini" / "antigravity" / "brain").mkdir(parents=True)
    assert antigravity.discover(None) == [], "沒設定就不該採集"
    assert antigravity.default_logs_dir().is_dir(), "但它說得出候選位置"
    assert "antigravity" in {c.key for c in detect_sources(_Cfg())}
