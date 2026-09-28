"""E5（ADR-033）：parser 搬進套件之後，使用端這一側要守住的東西。

套件自己的契約在 `packages/coding-agent-transcripts/tests/`（turn_key 公式、零相依、
樣本必須合成）。這一支只管**邊界**：轉接層有沒有做對、使用端有沒有偷偷長回相依。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "packages" / "coding-agent-transcripts"
SHIM = ROOT / "watchers" / "transcripts"
SUBMODULES = ("base", "antigravity", "claude_code", "claude_desktop", "codex", "drift")


def test_the_package_lives_in_the_repo_and_is_its_own_distribution():
    assert (PACKAGE_DIR / "pyproject.toml").is_file()
    assert (PACKAGE_DIR / "src" / "coding_agent_transcripts" / "__init__.py").is_file()
    # 它不是本 repo wheel 的一部分——`packages/` 沒有 __init__.py，setuptools 的
    # packages.find 因此看不到它。這條把「碰巧沒被收進去」變成「說好不收進去」。
    assert not (ROOT / "packages" / "__init__.py").exists()


def test_the_main_project_declares_the_package_as_a_dependency():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(metadata["project"]["dependencies"])
    assert "coding-agent-transcripts" in deps, "使用端要宣告相依，否則只是碰巧裝得到"
    assert "<1.0" in deps, "上限要鎖大版本（ADR-033 決策六）：大版本可以改介面，不該被動吃下"


# ---- 轉接層：要給的是「同一個模組」，不是「長得一樣的模組」 -----------------------


@pytest.mark.parametrize("name", SUBMODULES)
def test_the_shim_hands_back_the_very_same_module_object(name):
    """**這是 E5 實作時真的踩到的坑，所以它有一條專屬測試。**

    第一版轉接層是「每個子模組一個薄檔案，把套件的命名空間複製進來」（ADR-033 決策四原文）。
    三種 import 形式都過了，但漏掉**同一性**：複製品跟本尊是兩個物件，於是
    `monkeypatch.setattr(codex, "codex_home", …)` 打在複製品上，而 `discover()` 執行時
    去本尊的 globals 找——打樁完全無效。`test_transcript_parsers_and_drift.py` 的漂移測試
    當場紅給我看。改成 `sys.modules` 別名之後才成立。
    """
    import importlib

    shimmed = importlib.import_module(f"watchers.transcripts.{name}")
    real = importlib.import_module(f"coding_agent_transcripts.{name}")
    assert shimmed is real, (
        f"watchers.transcripts.{name} 不是 coding_agent_transcripts.{name} 本尊——"
        "打樁會失效，而且失效得很安靜"
    )


def test_every_old_import_form_still_works():
    """四種形式都要能用；少一種就是有人的程式碼會壞。"""
    script = (
        "from watchers.transcripts import SOURCES, SOURCE_KEYS;"
        "from watchers.transcripts import antigravity, claude_code, claude_desktop, codex;"
        "from watchers.transcripts.drift import DRIFT_WINDOW_DAYS, empty_drift, evaluate_drift;"
        "from watchers.transcripts.base import clean_prompt_text, is_cli_artifact;"
        "import watchers.transcripts.codex;"
        "print(len(SOURCES))"
    )
    out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "4"


def test_the_shim_holds_no_parser_source_of_its_own():
    """轉接層是轉接層。它一長出實作，兩份程式碼就開始漂移。"""
    files = sorted(p.name for p in SHIM.glob("*.py"))
    assert files == ["__init__.py"], f"轉接層只該有 __init__.py：{files}"
    source = (SHIM / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert defined == [], f"轉接層不得自己定義東西：{defined}"


def test_the_shim_says_when_it_should_be_deleted():
    """技術債要帶著到期條件，否則它會變成永久建築。"""
    source = (SHIM / "__init__.py").read_text(encoding="utf-8")
    assert "移除條件" in source


# ---- 使用端不得再有格式知識 -----------------------------------------------------


def test_core_desktop_sources_reexports_instead_of_keeping_a_second_copy():
    source = (ROOT / "core" / "desktop_sources.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "coding_agent_transcripts.desktop" in imported
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # 搬走的三支不准在這裡再有一份實作——兩份會漂移。
    assert defined == {"claude_desktop_cloud_cache_detected", "has_claude_desktop_project_logs"}, defined


def test_the_package_never_imports_the_consumer():
    """抽出來的重點就是不再相依 OmniContext；反向 import 一次就前功盡棄。"""
    src = PACKAGE_DIR / "src" / "coding_agent_transcripts"
    modules = sorted(src.glob("*.py"))
    assert len(modules) >= 8, f"掃描面太小（{[p.name for p in modules]}），會變成空轉"
    offenders = []
    for path in modules:
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
