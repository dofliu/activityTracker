from synthesizer.aggregator import format_context_for_prompt
from synthesizer.llm_client import LLMClient, diagnose_provider


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


def _day_data(ai_events):
    return {
        "git_events": [],
        "pr_events": [],
        "file_events": [],
        "ai_events": ai_events,
        "window_events": [],
    }


def _ai_event(prompt, response=""):
    return {
        "time": "2026-08-30 10:00",
        "platform": "claude_code",
        "tag": "activityTracker",
        "prompt": prompt,
        "response": response,
    }


def _patch_db_helpers(monkeypatch, max_prompt_chars=180_000):
    monkeypatch.setattr("synthesizer.aggregator.get_active_projects_list", lambda: [])
    monkeypatch.setattr("synthesizer.aggregator.get_open_loops_list", lambda: [])
    monkeypatch.setattr(
        "synthesizer.aggregator.get_config",
        lambda: DictConfig({"synthesizer": {"max_prompt_chars": max_prompt_chars}}),
    )


def test_giant_single_prompt_is_clipped_not_injected_verbatim(monkeypatch):
    """單筆 500k 字 prompt 曾直接塞進摘要 prompt，導致 Gemini 400 token 上限錯誤。"""
    _patch_db_helpers(monkeypatch)
    giant = "貼上論文全文" * 100_000  # ~600k chars
    rendered = format_context_for_prompt(
        _day_data([_ai_event(giant), _ai_event("正常提問", "簡短回應內容通過門檻")]),
        "2026-08-30",
    )
    assert len(rendered) < 200_000
    assert "截斷" in rendered
    assert "正常提問" in rendered


def test_excessive_event_lines_are_capped_with_explicit_omission(monkeypatch):
    _patch_db_helpers(monkeypatch)
    events = [_ai_event(f"提問 {i}") for i in range(500)]
    rendered = format_context_for_prompt(_day_data(events), "2026-08-30")
    assert "中間省略" in rendered
    assert "提問 0" in rendered  # 頭端保留
    assert "提問 499" in rendered  # 尾端保留
    assert rendered.count("問:") <= 210


def test_total_prompt_hard_cap_applies_after_section_caps(monkeypatch):
    _patch_db_helpers(monkeypatch, max_prompt_chars=20_000)
    events = [_ai_event("x" * 280, "y" * 240) for _ in range(200)]
    rendered = format_context_for_prompt(_day_data(events), "2026-08-30")
    assert len(rendered) <= 20_000 + 100
    assert "已於此截斷" in rendered


def test_fallback_message_distinguishes_missing_key_from_provider_rejection():
    client = LLMClient("gemini")

    missing_key = client._generate_fallback_summary(
        "context", "Gemini API key not found in environment variable 'GEMINI_API_KEY'"
    )
    assert "尚未設定 LLM API 金鑰" in missing_key

    too_large = client._generate_fallback_summary(
        "context",
        "400 INVALID_ARGUMENT. The input token count exceeds the maximum number "
        "of tokens allowed (1048576).",
    )
    assert "token 上限" in too_large
    assert "尚未設定 LLM API 金鑰" not in too_large

    generic = client._generate_fallback_summary("context", "ConnectionError: refused")
    assert "llm-test" in generic
    assert "尚未設定 LLM API 金鑰" not in generic


def test_diagnose_provider_reports_structure_without_secrets(monkeypatch):
    report = diagnose_provider("ollama", generate_test=False)
    assert report["provider"] == "ollama"
    assert "base_url" in report and "model" in report
    assert "reachable" in report
    if not report["reachable"]:
        assert "hint" in report
    assert "api_key" not in str(report)
