"""這個套件對外承諾的東西——壞了要立刻紅，不是等使用端的資料庫出事才發現。"""

from __future__ import annotations

import ast
import sys
from datetime import datetime
from pathlib import Path

import pytest

from coding_agent_transcripts import build_turn_key

# **掃的是原始碼樹，不是 site-packages 裡那份。** 第一版寫成
# `Path(coding_agent_transcripts.__file__).parent`，結果在已安裝的環境裡掃到的是安裝副本，
# 而 `samples/` 根本不在那底下——掃描於是靜悄悄失去目標。原始碼樹的位置是這支測試檔
# 自己的相對位置，那才是唯一不會被安裝方式影響的錨點。
REPO_PKG = Path(__file__).resolve().parent.parent
SRC = REPO_PKG / "src" / "coding_agent_transcripts"
SAMPLES = REPO_PKG / "samples"
MODULES = sorted(p for p in SRC.glob("*.py"))


# ---- turn_key 是對外契約 -------------------------------------------------------

# `turn_key` 是使用端的**跨重啟去重鍵**：變一個位元，既有使用者的資料庫就會把所有歷史
# 逐字稿當成新的重灌一次。所以它是對外契約，不是實作細節。
#
# **本來想寫死一個 sha256 字面值，實測之後否決了**：`build_turn_key` 會先 `Path(...).resolve()`，
# 而同一個字串 `/workspace/x.jsonl` 在 POSIX 上是 `/workspace/x.jsonl`、在 Windows 上是
# `\workspace\x.jsonl`——寫死的字面值在 Windows CI 必紅，而且紅的理由跟「有人改壞了」
# 完全無關。所以這裡鎖的是**公式**：平台、resolve 後的路徑、位置三者以 `|` 串接後取 sha256。
# 公式被改動時這條會紅，而平台差異不會讓它誤報。
#
# 「抽成套件前後 turn_key 沒變」是另一回事，由使用端那側的收據證明（ADR-033 Context 4）。


def test_turn_key_is_stable_for_the_same_inputs():
    """同一組輸入永遠算出同一個 key（而且與平台、路徑、行號三者都相關）。"""
    a = build_turn_key("claude_code", "/workspace/sample-project/session.jsonl", 1)
    b = build_turn_key("claude_code", "/workspace/sample-project/session.jsonl", 1)
    assert a == b and len(a) == 64

    assert a != build_turn_key("codex", "/workspace/sample-project/session.jsonl", 1)
    assert a != build_turn_key("claude_code", "/workspace/other/session.jsonl", 1)
    assert a != build_turn_key("claude_code", "/workspace/sample-project/session.jsonl", 2)


def test_turn_key_formula_is_pinned():
    """公式改了就紅——因為改它等於叫所有既有使用者重灌歷史。"""
    import hashlib

    platform, raw, position = "claude_code", "/workspace/sample-project/session.jsonl", 7
    expected = hashlib.sha256(
        f"{platform}|{Path(raw).resolve()}|{position}".encode("utf-8", errors="replace")
    ).hexdigest()
    assert build_turn_key(platform, raw, position) == expected, (
        "turn_key 的公式變了。這是使用端的去重鍵——改了等於叫所有既有使用者重灌歷史。"
    )


# ---- 零相依、零副作用 ----------------------------------------------------------


def test_the_package_has_no_third_party_imports():
    """零第三方相依是這個套件的賣點之一，靠掃描守著，不靠記得。"""
    stdlib = set(sys.stdlib_module_names)
    offenders = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in stdlib or root == "coding_agent_transcripts":
                    continue
                offenders.append(f"{path.name}:{node.lineno} {name}")
    assert offenders == [], f"套件不得有第三方相依：{offenders}"
    assert len(MODULES) >= 8, f"掃描面太小（{[p.name for p in MODULES]}），會變成空轉"


def test_the_package_never_imports_the_consumer():
    """抽出來的重點就是不再相依 OmniContext；反向 import 一次就前功盡棄。"""
    offenders = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module and module.split(".")[0] in {"core", "watchers", "rag", "web", "mcpserver"}:
                offenders.append(f"{path.name}:{node.lineno} {module}")
    assert offenders == [], f"套件不得反向相依使用端：{offenders}"


def test_parse_never_touches_a_database():
    """ADR-025 的那條線：parse 是產生器，不寫任何東西。"""
    banned = ("sqlalchemy", "sqlite3", "session_scope", "commit(", "session.add")
    offenders = []
    for path in MODULES:
        source = path.read_text(encoding="utf-8")
        for token in banned:
            if token in source:
                offenders.append(f"{path.name}: {token}")
    assert offenders == [], f"parser 不得碰資料庫：{offenders}"


def test_importing_the_package_writes_nothing_to_disk(tmp_path, monkeypatch):
    """import 一個解析函式庫不該在任何人的磁碟上留下東西。"""
    import subprocess

    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    subprocess.run(
        [sys.executable, "-c", "import coding_agent_transcripts, "
         "coding_agent_transcripts.codex, coding_agent_transcripts.desktop"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    assert set(tmp_path.rglob("*")) == before


# ---- 樣本必須是合成的 ----------------------------------------------------------


def test_the_shipped_samples_carry_no_real_prompts_paths_or_hostnames():
    """TODO E5 的判準之一。落地成**內容掃描**，不是一句叮嚀。"""
    import re
    import socket

    root = SAMPLES
    assert root.is_dir(), "樣本目錄找不到，這條會變成空轉"

    # 白名單：每一筆都要寫理由。目前是空的。
    allowed: set[str] = set()

    patterns = [
        re.compile(r"/home/(?!runner\b)[A-Za-z0-9._-]+"),
        re.compile(r"/Users/[A-Za-z0-9._-]+"),
        re.compile(r"[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
        re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
        re.compile(r"\bAIza[A-Za-z0-9_-]{10,}"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        re.compile(re.escape(socket.gethostname()), re.IGNORECASE) if socket.gethostname() else None,
    ]
    patterns = [p for p in patterns if p is not None]

    files = sorted(p for p in root.rglob("*") if p.is_file())
    assert files, "樣本目錄是空的"
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.findall(text):
                if match not in allowed:
                    hits.append(f"{path.name}: {match}")
    assert hits == [], f"樣本夾帶了真實資料：{hits}"


def test_the_scan_would_actually_catch_something(tmp_path):
    """非空轉證明：把會咬人的字串放進一個假樣本，上面那條規則要抓得到。"""
    import re

    hostile = "/home/victim/.ssh/id_rsa 與 sk-abcdefghijklmnop"
    patterns = [re.compile(r"/home/(?!runner\b)[A-Za-z0-9._-]+"), re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")]
    assert [p.findall(hostile) for p in patterns] == [["/home/victim"], ["sk-abcdefghijklmnop"]]
