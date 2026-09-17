"""每平台一個 parser ＋「檔案在動、事件是零」的漂移警示（ADR-025，TODO D9）。

D9 之前，`watchers/agent_log_watcher.py` 是 1,066 行：服務骨架與四個平台、五套 parser 疊在一起，
而且四種格式都是別家工具的私有格式——格式一變，parser 不會拋例外，它會正常跑完、產出零筆事件。

這裡把兩件事寫成測試：

1. 分層：每個 parser 模組 < 300 行、`parse` 不碰資料庫、服務不認識任何格式。
2. 漂移：只有「檔案有更新」**且**「視窗內零事件」才警示；沒用過、關掉、目錄不存在都不警示，
   而警示一旦成立，要一路走到系統健康頁並把採集器標成 degraded。
"""

from __future__ import annotations

import ast
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import AIPromptEvent, Base
from watchers.agent_log_watcher import AgentLogWatcherService
from watchers.transcripts import SOURCES, SOURCE_KEYS
from watchers.transcripts import antigravity, claude_code, claude_desktop, codex
from watchers.transcripts.drift import DRIFT_WINDOW_DAYS, empty_drift, evaluate_drift

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "watchers" / "transcripts"
NOW = datetime(2026, 9, 17, 9, 0)

PARSER_MODULES = ("claude_code", "claude_desktop", "codex", "antigravity")


class TempDB:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class DictConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def get_path(self, key_path, default=None):
        raw = self.get(key_path)
        return Path(raw) if raw else default

    def expand_path(self, raw):
        return Path(raw)


# ---- 1. 分層 ---------------------------------------------------------------


@pytest.mark.parametrize("module", PARSER_MODULES)
def test_every_parser_module_stays_under_300_lines(module):
    """一個平台一個檔案，小到能整個讀完——這就是 D9 想換到的東西。"""
    path = PKG / f"{module}.py"
    assert path.exists(), f"{module}.py 不見了"
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count < 300, f"{module}.py 有 {line_count} 行"


@pytest.mark.parametrize("module", PARSER_MODULES)
def test_parsers_never_touch_the_database(module):
    """`parse` 只描述輪次，寫入是服務的事——所以 parser 能在沒有資料庫的情況下單獨測。"""
    source = (PKG / f"{module}.py").read_text(encoding="utf-8")
    for forbidden in ("get_db", "session_scope", "AIPromptEvent", "IngestionCheckpoint"):
        assert forbidden not in source, f"{module}.py 碰了 {forbidden}"


def test_the_service_no_longer_knows_any_transcript_format():
    """服務只剩執行緒、checkpoint、寫入與診斷；格式關鍵字一個都不該出現在裡面。"""
    source = (ROOT / "watchers" / "agent_log_watcher.py").read_text(encoding="utf-8")
    for format_token in ("PLANNER_RESPONSE", "USER_REQUEST", "session_meta", "final_answer",
                         "stop_reason", "tool_result", "history.jsonl", ".codex"):
        assert format_token not in source, f"服務還認得 {format_token}"


def test_every_source_implements_the_same_two_functions():
    assert SOURCE_KEYS == ("claude_code", "claude_desktop", "codex", "antigravity")
    for source in SOURCES:
        assert callable(source.discover) and callable(source.parse), source.key
        # 兩個函式的關鍵字參數是共同介面的一部分；服務一視同仁地呼叫它們。
        for func in (source.discover, source.parse):
            params = func.__code__.co_varnames[: func.__code__.co_argcount + func.__code__.co_kwonlyargcount]
            assert "full_history" in params or "cfg" in params, f"{source.key}: {func.__name__}"
            assert "now" in params, f"{source.key}: {func.__name__} 少了 now"


def test_claude_desktop_reuses_the_claude_code_format_on_purpose():
    """Desktop 寫的就是同一種 JSONL——這個 import 是事實的反映，不是複製貼上的替代品。"""
    source = (PKG / "claude_desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "watchers.transcripts.claude_code" in imported


# ---- 2. parser 行為（不碰資料庫）-------------------------------------------


def test_codex_rollout_parser_yields_turns_without_a_database(tmp_path):
    source = tmp_path / "rollout.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "s1", "cwd": str(tmp_path)}},
        {"type": "response_item", "timestamp": "2026-09-17T01:00:00Z",
         "payload": {"role": "user", "content": "這一輪要做什麼"}},
        {"type": "response_item", "timestamp": "2026-09-17T01:05:00Z",
         "payload": {"role": "assistant", "phase": "final_answer", "content": "先把 parser 拆開"}},
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    turns = list(codex.parse_jsonl_session(source))
    assert len(turns) == 1
    turn = turns[0]
    assert turn.platform == "codex" and turn.conv_id == "s1"
    assert turn.response == "先把 parser 拆開" and turn.response_status == "final_candidate"
    assert turn.source_position == 2 and turn.source_path == str(source.resolve())
    assert turn.evidence is not None and turn.evidence.completion_evidence_kind == "codex_final_answer"


def test_antigravity_parser_unwraps_the_request_and_skips_injected_messages(tmp_path):
    source = tmp_path / "conv" / "step" / "transcript.jsonl"
    source.parent.mkdir(parents=True)
    rows = [
        {"type": "USER_INPUT", "created_at": "2026-09-17T02:00:00Z",
         "content": "<USER_REQUEST>幫我看 D9</USER_REQUEST>"},
        {"type": "USER_INPUT", "created_at": "2026-09-17T02:01:00Z",
         "content": "<SYSTEM_MESSAGE>internal</SYSTEM_MESSAGE>"},
        {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "已經拆成四個模組了"},
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    turns = list(antigravity.parse(source))
    assert [t.prompt for t in turns] == ["幫我看 D9"]
    assert turns[0].response == "已經拆成四個模組了"
    assert turns[0].response_status == "final_candidate"
    # url 保留未 resolve 的路徑、source_path 用 resolve 過的——與拆分前逐字相同
    assert turns[0].url == str(source) and turns[0].source_path == str(source.resolve())
    assert turns[0].evidence is None  # 這個平台沒有可驗證的 start／final 配對


def test_claude_code_falls_back_to_history_only_when_there_are_no_project_logs(tmp_path):
    cfg = DictConfig({"watchers": {"agent_log_watcher": {"claude_code_logs_path": str(tmp_path)}}})
    (tmp_path / "history.jsonl").write_text(
        json.dumps({"display": "只有提問", "timestamp": "2026-09-17T03:00:00Z"}) + "\n", encoding="utf-8"
    )
    assert [p.name for p in claude_code.discover(cfg)] == ["history.jsonl"]
    turns = list(claude_code.parse(tmp_path / "history.jsonl"))
    assert [t.response_status for t in turns] == ["missing"]
    assert turns[0].dedupe_key  # 行程內去重用，跨重啟靠 turn_key

    project = tmp_path / "projects" / "demo" / "session.jsonl"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"type": "user", "message": {"content": "有 projects 了"},
                                   "timestamp": "2026-09-17T04:00:00Z"}) + "\n", encoding="utf-8")
    assert [p.name for p in claude_code.discover(cfg)] == ["session.jsonl"]


def test_claude_desktop_initial_lookback_skips_ancient_session_copies(tmp_path):
    logs_dir = tmp_path / "local-agent-mode-sessions"
    old = logs_dir / "w" / "s" / "old" / ".claude" / "projects" / "p" / "a.jsonl"
    fresh = logs_dir / "w" / "s" / "fresh" / ".claude" / "projects" / "p" / "b.jsonl"
    for path in (old, fresh):
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
    ancient = time.time() - 60 * 86400
    os.utime(old, (ancient, ancient))

    cfg = DictConfig({"watchers": {"agent_log_watcher": {"claude_desktop_logs_path": str(logs_dir)}}})
    assert [p.name for p in claude_desktop.discover(cfg)] == ["b.jsonl"]
    assert sorted(p.name for p in claude_desktop.discover(cfg, full_history=True)) == ["a.jsonl", "b.jsonl"]


# ---- 3. 漂移判定（純函式）---------------------------------------------------


def test_drift_needs_both_halves_files_moving_and_events_at_zero():
    recent = NOW - timedelta(hours=6)
    stale = NOW - timedelta(days=30)

    drift = evaluate_drift(
        newest_file_at={
            "codex": recent,        # 檔案在動、事件是零 → 警示
            "claude_code": recent,  # 檔案在動、視窗內有事件 → parser 還活著
            "antigravity": stale,   # 沒在用 → 不是故障
        },
        last_event_at={"claude_code": NOW - timedelta(hours=5), "codex": stale},
        now=NOW,
    )
    assert [item["platform"] for item in drift["platforms"]] == ["codex"]
    assert drift["window_days"] == DRIFT_WINDOW_DAYS
    assert drift["platforms"][0]["days_silent"] == 30.0
    assert drift["platforms"][0]["last_event_at"] == stale.isoformat(timespec="seconds")


def test_a_platform_that_never_produced_an_event_still_needs_moving_files():
    # 從沒採到過東西：檔案在動就警示（days_silent 無從計算，如實回 None）
    drifted = evaluate_drift(
        newest_file_at={"codex": NOW - timedelta(hours=1)}, last_event_at={}, now=NOW,
    )
    assert drifted["platforms"][0]["days_silent"] is None
    # 檔案也沒在動：什麼都不說
    quiet = evaluate_drift(
        newest_file_at={"codex": NOW - timedelta(days=90)}, last_event_at={}, now=NOW,
    )
    assert quiet["platforms"] == []
    # 探索不到檔案（目錄不存在）：同樣不說
    missing = evaluate_drift(newest_file_at={"codex": None}, last_event_at={}, now=NOW)
    assert missing["platforms"] == []


def test_empty_drift_has_the_full_shape_before_the_first_scan():
    assert empty_drift() == {"window_days": DRIFT_WINDOW_DAYS, "checked_at": None, "platforms": []}


# ---- 4. 從服務到系統健康頁 --------------------------------------------------


def _service_with_codex_drift(monkeypatch, tmp_path, *, with_event: bool):
    """真的擺一個剛更新的 Codex rollout，但讓 parser 什麼都不產出（模擬格式漂移）。"""
    session_file = tmp_path / "sessions" / "2026" / "rollout.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"type": "unknown_new_shape"}) + "\n", encoding="utf-8")

    database = TempDB()
    if with_event:
        with database.session_scope() as session:
            session.add(AIPromptEvent(platform="codex", prompt_text="昨天問過的", timestamp=datetime.now()))

    import watchers.agent_log_watcher as watcher_module

    monkeypatch.setattr(watcher_module, "get_db", lambda: database)
    monkeypatch.setattr(codex, "codex_home", lambda: tmp_path)

    service = AgentLogWatcherService()
    monkeypatch.setattr(
        service, "cfg",
        DictConfig({"watchers": {"agent_log_watcher": {
            "claude_code": False, "claude_desktop": False, "antigravity": False, "codex": True,
        }}}),
    )
    monkeypatch.setattr(watcher_module, "get_config", lambda: service.cfg)
    return service, database


def test_a_silently_broken_parser_shows_up_as_drift_not_as_healthy(monkeypatch, tmp_path):
    service, _ = _service_with_codex_drift(monkeypatch, tmp_path, with_event=False)
    service.scan_all_agents(full_history=False)

    diagnostics = service.get_diagnostics()
    # 解析沒拋例外，所以來源自己是 healthy——這正是漂移警示要補上的盲點
    assert diagnostics["sources"]["codex"]["state"] == "healthy"
    assert [item["platform"] for item in diagnostics["drift"]["platforms"]] == ["codex"]
    assert diagnostics["sources"]["claude_code"]["state"] == "disabled"
    # 關掉的來源不參與判定
    assert "claude_code" not in {item["platform"] for item in diagnostics["drift"]["platforms"]}


def test_a_working_parser_produces_no_drift_alert(monkeypatch, tmp_path):
    service, _ = _service_with_codex_drift(monkeypatch, tmp_path, with_event=True)
    service.scan_all_agents(full_history=False)
    assert service.get_diagnostics()["drift"]["platforms"] == []


class _StubAgentWatcher:
    """一個「解析沒報錯、但被漂移警示抓到」的採集器。"""

    def __init__(self, drift_platforms):
        self._thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        self._thread.start()
        self._drift_platforms = drift_platforms

    def get_diagnostics(self):
        return {
            "state": "healthy",
            "sources": {key: {"state": "healthy"} for key in SOURCE_KEYS},
            "drift": {"window_days": DRIFT_WINDOW_DAYS, "checked_at": None,
                      "platforms": self._drift_platforms},
        }


@pytest.mark.parametrize("drifted", [True, False])
def test_drift_reaches_the_system_health_page_as_a_degraded_collector(monkeypatch, drifted):
    """健康頁讀的是 collector_health；漂移必須讓它變色，而不是只躺在 diagnostics 裡。

    另一半同樣重要：來源自陳 `healthy` 且沒有漂移時，這條規則不准自己把採集器染紅。
    """
    from core.manager import WatcherManager

    platforms = (
        [{"platform": "codex", "newest_file_at": None, "last_event_at": None, "days_silent": 9.0}]
        if drifted else []
    )
    import core.manager as manager_module

    # 這一項是否啟用由設定決定，而設定會被同批測試改動；這裡只固定住它，其餘照實讀。
    real_cfg = manager_module.get_config()

    class _AgentEnabledConfig:
        def __getattr__(self, name):
            return getattr(real_cfg, name)

        def get(self, key_path, default=None):
            if key_path == "watchers.agent_log_watcher.enabled":
                return True
            return real_cfg.get(key_path, default)

    monkeypatch.setattr(manager_module, "get_config", lambda: _AgentEnabledConfig())

    manager = WatcherManager()
    manager._is_running = True
    monkeypatch.setattr(manager, "agent_log_watcher", _StubAgentWatcher(platforms))

    status = manager.get_status()
    assert status["collector_runtime"]["agent_log_watcher"] == "running"
    assert status["collector_diagnostics"]["agent_log_watcher"]["drift"]["platforms"] == platforms
    if drifted:
        assert status["collector_health"]["agent_log_watcher"] == "degraded"
        assert "agent_log_watcher" in status["degraded_collectors"]
    else:
        # 可能是 healthy／idle／stale——取決於本機有沒有近期事件；重點是這條規則沒插手
        assert status["collector_health"]["agent_log_watcher"] != "degraded"


def test_the_web_health_page_renders_the_drift_alert():
    """兩個渲染點都要說得出是哪個平台——使用者不該只看到一顆變紅的燈。"""
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert app_js.count("drift.platforms") + app_js.count("(it.d.drift || {}).platforms") >= 1
    assert "疑似格式漂移" in app_js          # 採集器列表
    assert "疑似 transcript 格式漂移" in app_js  # 系統健康頁的診斷卡
    assert "格式漂移偵測" in app_js
