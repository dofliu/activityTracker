import copy
import json

from core.secretary_advisor import (
    annotate_action_proposals,
    reset_advisor_cache,
)


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


def _config(enabled=True, provider="ollama", cache_minutes=10):
    return DictConfig(
        {
            "proactive_secretary": {
                "llm_advisor": {
                    "enabled": enabled,
                    "provider": provider,
                    "timeout_seconds": 20,
                    "cache_minutes": cache_minutes,
                }
            },
            "synthesizer": {"ollama": {"model": "llama3.1:8b"}},
        }
    )


def _result():
    return {
        "status": "proposal_only",
        "mode": "proposal_only",
        "proposals": [
            {
                "proposal_id": "aaa111",
                "proposal_type": "aging_pr",
                "project_key": "activityTracker",
                "subject_ref": "pr:activityTracker#9",
                "title": "activityTracker#9",
                "detail": "fix parser",
                "reason": "開啟超過 14 天",
                "suggested_action": "確認這個 PR 還要不要。",
                "priority": "medium",
                "risk_level": "L0_READ_ONLY",
                "execution_available": False,
                "url": "https://github.com/x/y/pull/9",
                "age_days": 14.0,
                "evidence_refs": ["pr:activityTracker#9"],
                "score": 0.6,
            },
            {
                "proposal_id": "bbb222",
                "proposal_type": "stalled_open_loop",
                "project_key": "AI_Papers",
                "subject_ref": "loops:AI_Papers",
                "title": "AI_Papers",
                "detail": "",
                "reason": "未結事項停滯 72 小時",
                "suggested_action": "先看 Context Handoff 再決定。",
                "priority": "high",
                "risk_level": "L0_READ_ONLY",
                "execution_available": False,
                "url": None,
                "age_days": 3.0,
                "evidence_refs": ["open_loop:12"],
                "score": 0.9,
            },
        ],
        "execution_available": False,
        "cloud_llm_used": False,
        "query_persisted": False,
    }


def _valid_llm_reply():
    return json.dumps(
        {
            "summary": "今天先收掉 AI_Papers 的未結事項，PR 可以排在下午。",
            "annotations": [
                {
                    "proposal_id": "bbb222",
                    "note": "停滯三天且分數最高，建議最先處理。",
                    "priority_hint": "high",
                },
                {"proposal_id": "unknown999", "note": "幻覺項目", "priority_hint": "low"},
                {"proposal_id": "aaa111", "note": "x" * 999, "priority_hint": "urgent"},
            ],
        },
        ensure_ascii=False,
    )


def test_disabled_advisor_is_pure_passthrough():
    reset_advisor_cache()
    result = _result()
    baseline = copy.deepcopy(result)

    annotated = annotate_action_proposals(result, cfg=_config(enabled=False))

    assert annotated["advisor"]["status"] == "disabled"
    assert annotated["advisor"]["enabled"] is False
    assert all("llm_note" not in item for item in annotated["proposals"])
    without_advisor = {k: v for k, v in annotated.items() if k != "advisor"}
    assert without_advisor == baseline


def test_annotations_only_attach_to_known_ids_and_are_clamped():
    reset_advisor_cache()
    calls = []

    def fake_generate(system_prompt, user_prompt):
        calls.append(user_prompt)
        return _valid_llm_reply()

    result = annotate_action_proposals(
        _result(), cfg=_config(), llm_generate=fake_generate
    )

    advisor = result["advisor"]
    assert advisor["status"] == "annotated"
    assert advisor["annotated"] == 2
    assert "AI_Papers" in advisor["summary"]
    by_id = {item["proposal_id"]: item for item in result["proposals"]}
    assert by_id["bbb222"]["llm_note"].startswith("停滯三天")
    assert by_id["bbb222"]["llm_priority_hint"] == "high"
    # 超長 note 被截斷、非法 priority_hint 被丟棄
    assert len(by_id["aaa111"]["llm_note"]) == 300
    assert "llm_priority_hint" not in by_id["aaa111"]
    # 幻覺 id 不會產生新 proposal
    assert set(by_id) == {"aaa111", "bbb222"}
    # deterministic 欄位不可被改動
    assert by_id["aaa111"]["suggested_action"] == "確認這個 PR 還要不要。"
    assert result["execution_available"] is False
    assert result["cloud_llm_used"] is False  # ollama 屬本機


def test_prompt_only_contains_whitelisted_fields():
    reset_advisor_cache()
    captured = {}

    def fake_generate(system_prompt, user_prompt):
        captured["user"] = user_prompt
        return _valid_llm_reply()

    annotate_action_proposals(_result(), cfg=_config(), llm_generate=fake_generate)

    assert "evidence_refs" not in captured["user"]
    assert "https://github.com" not in captured["user"]
    assert "subject_ref" not in captured["user"]
    assert "risk_level" not in captured["user"]
    assert "aaa111" in captured["user"]  # proposal_id 必須在，LLM 才能對應


def test_cloud_provider_flips_cloud_llm_used_flag():
    reset_advisor_cache()
    cfg = _config(provider="gemini")
    cfg.data["synthesizer"]["gemini"] = {"model": "gemini-2.5-flash"}

    result = annotate_action_proposals(
        _result(), cfg=cfg, llm_generate=lambda s, u: _valid_llm_reply()
    )

    assert result["advisor"]["status"] == "annotated"
    assert result["cloud_llm_used"] is True


def test_invalid_json_and_exception_fall_back_to_deterministic():
    reset_advisor_cache()
    baseline = copy.deepcopy(_result())

    bad_json = annotate_action_proposals(
        _result(), cfg=_config(), llm_generate=lambda s, u: "抱歉我幫不上忙"
    )
    assert bad_json["advisor"]["status"] == "fallback_deterministic"
    assert bad_json["advisor"]["fallback_reason"] == "invalid_json"
    assert all("llm_note" not in item for item in bad_json["proposals"])
    assert {k: v for k, v in bad_json.items() if k != "advisor"} == baseline

    reset_advisor_cache()

    def boom(system_prompt, user_prompt):
        raise TimeoutError("advisor timed out")

    failed = annotate_action_proposals(_result(), cfg=_config(), llm_generate=boom)
    assert failed["advisor"]["status"] == "fallback_deterministic"
    assert failed["advisor"]["fallback_reason"] == "TimeoutError"
    assert failed["cloud_llm_used"] is False


def test_fallback_markdown_embedding_payload_json_is_not_treated_as_annotation():
    """LLMClient 失敗時回傳含原 payload 的備援 markdown；不得誤判為 annotated。"""
    reset_advisor_cache()

    def echoing_fallback(system_prompt, user_prompt):
        return f"# [本機備援模式]\n\n```text\n{user_prompt[:2500]}\n```\n"

    result = annotate_action_proposals(
        _result(), cfg=_config(), llm_generate=echoing_fallback
    )

    assert result["advisor"]["status"] == "fallback_deterministic"
    assert result["advisor"]["fallback_reason"] == "no_usable_annotations"
    assert all("llm_note" not in item for item in result["proposals"])

    # 失敗結果不得被 cache：下一次成功呼叫必須真的打到 LLM
    hit = annotate_action_proposals(
        _result(), cfg=_config(), llm_generate=lambda s, u: _valid_llm_reply()
    )
    assert hit["advisor"]["status"] == "annotated"


def test_cache_avoids_repeated_llm_calls_for_same_payload():
    reset_advisor_cache()
    call_count = {"n": 0}

    def counting_generate(system_prompt, user_prompt):
        call_count["n"] += 1
        return _valid_llm_reply()

    cfg = _config(cache_minutes=10)
    first = annotate_action_proposals(_result(), cfg=cfg, llm_generate=counting_generate)
    second = annotate_action_proposals(_result(), cfg=cfg, llm_generate=counting_generate)

    assert call_count["n"] == 1
    assert first["advisor"]["status"] == "annotated"
    assert second["advisor"]["status"] == "cached"
    assert second["proposals"][1]["llm_note"] == first["proposals"][1]["llm_note"]


def test_no_proposals_is_skipped_without_llm_call():
    reset_advisor_cache()

    def must_not_call(system_prompt, user_prompt):
        raise AssertionError("advisor must not run without proposals")

    empty = {"status": "proposal_only", "proposals": [], "cloud_llm_used": False}
    result = annotate_action_proposals(empty, cfg=_config(), llm_generate=must_not_call)
    assert result["advisor"]["status"] == "skipped_no_proposals"
