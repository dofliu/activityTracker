"""四種格式各解一次，外加幾條「壞掉時要壞得大聲」的契約。"""

from __future__ import annotations

from datetime import datetime

import pytest

from coding_agent_transcripts import (
    SOURCE_KEYS,
    SOURCES,
    DictConfig,
    antigravity,
    claude_code,
    claude_desktop,
    codex,
)

FIXED_NOW = datetime(2026, 1, 3, 0, 0, 0)


def _now():
    return FIXED_NOW


def test_four_platforms_are_registered_in_scan_order():
    assert SOURCE_KEYS == ("claude_code", "claude_desktop", "codex", "antigravity")
    assert len(SOURCES) == 4
    for source in SOURCES:
        assert callable(source.discover) and callable(source.parse)
        assert source.label and source.key


def test_claude_code_sample_parses_every_user_turn(samples):
    """**parser 不替使用端做過濾。**

    第一版這支測試寫錯了：我假設 `<system-reminder>` 會在 parse 階段被丟掉。實際上
    `is_cli_artifact()` 是**給使用端用的工具**，不是 parser 自己會套的規則——parse 的職責
    是「檔案裡有什麼就報什麼」，要不要濾是使用端的政策（ADR-025 的同一條線：
    parser 不做決定）。所以樣本裡那一行 CLI 內部訊息會照實出現在這裡，
    由下一支測試證明使用端**有辦法**把它濾掉。
    """
    path = samples / "claude_code" / "projects" / "sample-project" / "session-0001.jsonl"
    turns = list(claude_code.parse(path, now=_now))
    assert [t.prompt for t in turns] == [
        "示範提問：把排序改成穩定排序",
        "<system-reminder>這行是 CLI 內部訊息，應該被濾掉</system-reminder>",
        "示範提問二：順便補一個 README",
    ]
    first = turns[0]
    assert first.platform == "claude_code"
    assert first.response == "示範回應：已改成穩定排序，並補了一個測試。"
    # 下一個 user turn 封閉了前一輪，所以第一輪是 final_candidate
    assert first.response_status == "final_candidate"
    # 最後一輪沒有回應，而且後面沒有東西封閉它
    assert turns[-1].response_status == "missing"


def test_the_consumer_can_filter_cli_artifacts_with_the_shipped_helper(samples):
    """過濾是使用端的政策，但套件要**給得出工具**——否則每個使用端都得自己維護一份前綴表。"""
    from coding_agent_transcripts import is_cli_artifact

    path = samples / "claude_code" / "projects" / "sample-project" / "session-0001.jsonl"
    kept = [t.prompt for t in claude_code.parse(path, now=_now) if not is_cli_artifact(t.prompt)]
    assert kept == ["示範提問：把排序改成穩定排序", "示範提問二：順便補一個 README"]


def test_claude_desktop_reads_the_same_format_from_its_own_layout(samples):
    root = samples / "claude_desktop"
    cfg = DictConfig({"watchers.agent_log_watcher.claude_desktop_logs_path":
                      str(root / "local-agent-mode-sessions")})
    found = claude_desktop.discover(cfg, full_history=True, now=_now)
    assert len(found) == 1, found
    turns = list(claude_desktop.parse(found[0], now=_now))
    assert turns and turns[0].platform == "claude_desktop"
    assert turns[0].prompt == "示範提問：把排序改成穩定排序"


def test_codex_final_answer_beats_the_earlier_commentary(samples):
    path = next((samples / "codex").rglob("rollout-*.jsonl"))
    turns = list(codex.parse_jsonl_session(path, now=_now))
    assert len(turns) == 1
    turn = turns[0]
    assert turn.platform == "codex"
    assert turn.response == "示範回應：早退的那個分支沒有 return。"
    assert turn.response_status == "final_candidate"
    # 背景任務的時間證據來自來源本身，不是掃描時間
    assert turn.evidence is not None
    assert turn.evidence.started_at == datetime(2026, 1, 2, 3, 0, 0)


def test_antigravity_unwraps_the_request_and_ignores_tool_chatter(samples):
    path = samples / "antigravity" / "conversation-0003" / "steps" / "transcript.jsonl"
    turns = list(antigravity.parse(path, now=_now))
    assert len(turns) == 1
    turn = turns[0]
    assert turn.prompt == "示範提問：幫我畫一張流程圖", "<USER_REQUEST> 沒有脫殼"
    assert turn.response == "示範回應：流程圖已經產生在 docs 底下。"
    assert turn.response_status == "final_candidate", "status=DONE 應該算明確 final"
    assert turn.conv_id == "conversation-0003"


def test_every_sample_turn_carries_provenance(samples):
    cases = [
        (claude_code, samples / "claude_code" / "projects" / "sample-project" / "session-0001.jsonl"),
        (codex, next((samples / "codex").rglob("rollout-*.jsonl"))),
        (antigravity, samples / "antigravity" / "conversation-0003" / "steps" / "transcript.jsonl"),
    ]
    for module, path in cases:
        for turn in module.parse(path, now=_now):
            assert turn.turn_key, f"{module.PLATFORM} 少了 turn_key"
            assert turn.source_path, f"{module.PLATFORM} 少了 source_path"
            assert turn.source_position is not None, f"{module.PLATFORM} 少了 source_position"
            assert isinstance(turn.timestamp, datetime)


def test_malformed_jsonl_raises_instead_of_silently_skipping(samples, tmp_path):
    """壞行被靜默跳過，使用端的 checkpoint 就會帶著缺口往前移——那是最難查的一種資料遺失。"""
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"type": "user"}\n{這不是 JSON}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed JSONL"):
        list(claude_code.parse(broken, now=_now))


def test_discover_works_without_any_config():
    """外部使用者不必生出一個 Config 物件（ADR-033 決策三）。"""
    for source in SOURCES:
        assert isinstance(source.discover(None), list)
