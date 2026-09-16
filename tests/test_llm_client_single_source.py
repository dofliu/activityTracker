"""LLM client 只有一份（TODO D2，ROADMAP §13 R1）。

2026-09-16 之前 `synthesizer/llm_client.py`（同步）與 `rag/llm_gateway.py`（串流）各自實作四個
provider，預設模型漂移（gemini-2.5 vs 3.7）、金鑰解析寫了四份。這裡鎖住：

1. 舊模組不存在；預設模型名只在 `core/llm_client.py` 出現一次（其他程式不得再寫字面）。
2. 同步 `generate` 與串流 `stream_chat` 用同一組預設模型與金鑰環境變數。
3. Ollama 兩條路徑都走 `/api/chat`，system prompt 以 system role 傳，不再拼 `<system>` 標籤。
4. 失敗抬頭字面不變——`core/meeting_transcripts.looks_like_llm_error` 與 `core/acceptance.py`
   靠它們辨識供應商錯誤。
5. `omni ask` 的第三條 Ollama 呼叫與 `rag/embeddings.py` 的第四份金鑰解析都改走這裡。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from core import llm_client as module
from core.llm_client import (
    DEFAULT_MODELS,
    FALLBACK_PREFIX,
    LLMClient,
    default_model,
    normalize_provider,
    resolve_provider_api_key,
)

ROOT = Path(__file__).resolve().parents[1]


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


def test_old_client_modules_are_gone():
    assert not (ROOT / "synthesizer" / "llm_client.py").exists()
    assert not (ROOT / "rag" / "llm_gateway.py").exists()


def test_default_model_literals_live_only_in_llm_client():
    """任何程式碼（測試與前端下拉選單除外）不得再寫死模型名；要改預設就改 DEFAULT_MODELS。"""
    literal = re.compile(r"gemini-\d|claude-3|gpt-4o|llama3\.\d")
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tests/") or rel == "core/llm_client.py" or "/.venv" in rel or "site-packages" in rel:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if literal.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}")
    assert offenders == [], offenders
    assert set(DEFAULT_MODELS) == {"ollama", "gemini", "anthropic", "openai"}


def test_provider_aliases_collapse_to_four_names(monkeypatch):
    monkeypatch.setattr(module, "get_config", lambda: DictConfig({"synthesizer": {"provider": "gemini"}}))
    assert normalize_provider("gpt") == "openai"
    assert normalize_provider("Claude") == "anthropic"
    assert normalize_provider("google") == "gemini"
    assert normalize_provider(None) == "gemini"  # 空值取 synthesizer.provider
    assert LLMClient("GPT").provider == "openai"


def test_sync_and_stream_share_model_defaults_and_key_env(monkeypatch):
    cfg = DictConfig({"synthesizer": {"gemini": {"model": "gemini-x", "api_key_env": "MY_GEMINI"}}})
    monkeypatch.setattr(module, "get_config", lambda: cfg)
    assert default_model("gemini") == "gemini-x"
    assert default_model("openai") == DEFAULT_MODELS["openai"]
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("MY_GEMINI", "k-1")
    assert resolve_provider_api_key("gemini") == "k-1"
    # 串流缺金鑰的提示要指向**設定裡的**環境變數名，而不是寫死 GEMINI_API_KEY
    monkeypatch.delenv("MY_GEMINI", raising=False)

    async def _collect():
        return "".join([t async for t in LLMClient("gemini", cfg).stream_chat([{"role": "user", "content": "hi"}])])

    text = asyncio.run(_collect())
    assert text.startswith("【尚未偵測到") and "MY_GEMINI" in text


def test_ollama_generate_uses_chat_endpoint_with_system_role(monkeypatch):
    calls = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"role": "assistant", "content": "  OK  "}}

    def _post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _post)
    cfg = DictConfig({"synthesizer": {"ollama": {"base_url": "http://127.0.0.1:11434/", "model": "m1"}}})
    reply = LLMClient("ollama", cfg).generate("SYS", "USER")
    assert reply == "  OK  "
    url, payload, timeout = calls[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert payload["model"] == "m1" and payload["stream"] is False
    assert payload["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert "<system>" not in str(payload)


def test_generate_returns_fallback_markdown_instead_of_raising(monkeypatch):
    """日報永遠要能產生：失敗回備援 markdown，抬頭是下游辨識用的字面。"""
    import requests

    def _boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", _boom)
    cfg = DictConfig({"synthesizer": {"ollama": {"base_url": "http://127.0.0.1:1"}}})
    text = LLMClient("ollama", cfg).generate("s", "u")
    assert text.startswith(FALLBACK_PREFIX) and "llm-test" in text

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    missing = LLMClient("gemini", DictConfig()).generate("s", "u")
    assert "尚未設定 LLM API 金鑰" in missing


def test_error_markers_still_match_downstream_detectors():
    """事實閘與驗收中心靠這些抬頭辨識供應商錯誤；client 產生的字面必須被它們接住。"""
    from core.acceptance import _LLM_ERROR_MARKERS as acceptance_markers
    from core.meeting_transcripts import looks_like_llm_error

    async def _unknown():
        return "".join([t async for t in LLMClient("ollama", DictConfig()).stream_chat([], provider="nope")])

    unknown = asyncio.run(_unknown())
    assert unknown.startswith("[LLMGateway 錯誤]")
    assert looks_like_llm_error(unknown)
    assert any(marker in unknown for marker in acceptance_markers)
    assert looks_like_llm_error(FALLBACK_PREFIX + " x")
    assert looks_like_llm_error("【尚未偵測到 OpenAI API Key…】")
    for prefix in ("[OpenAI API 錯誤]", "[Claude API 錯誤]", "[Ollama 連線錯誤"):
        assert looks_like_llm_error(prefix + ": boom")


def test_semantic_index_answer_goes_through_the_single_client(monkeypatch):
    """`omni ask` 原本自己 POST /api/generate（第三份 Ollama 實作）。"""
    import core.semantic_index as si

    seen = {}

    def _fake_chat(self, messages, *, model=None, base_url=None, options=None, timeout=120):
        seen.update(messages=messages, model=model, base_url=base_url, options=options, timeout=timeout)
        return "答案 [S1]"

    monkeypatch.setattr(LLMClient, "ollama_chat", _fake_chat)
    cfg = DictConfig({"synthesizer": {"ollama": {"base_url": "http://127.0.0.1:11434", "model": "m"}}})
    sources = [{"citation": "S1", "source_ref": "r", "trust_status": "t", "embedding_input_mode": "e",
                "project_key": "p", "source_updated_at": None, "excerpt": "x"}]
    answer, model = si._generate_local_answer("q", sources, cfg)
    assert answer == "答案 [S1]" and model == "m"
    assert seen["base_url"].startswith("http://127.0.0.1") and seen["options"] == {"temperature": 0.1}
    assert seen["messages"][0]["role"] == "user" and "Evidence" in seen["messages"][0]["content"]

    monkeypatch.setattr(LLMClient, "ollama_chat", lambda self, *a, **k: "   ")
    with pytest.raises(RuntimeError):
        si._generate_local_answer("q", sources, cfg)


def test_embeddings_reuse_the_shared_key_resolution():
    source = (ROOT / "rag" / "embeddings.py").read_text(encoding="utf-8")
    assert "resolve_provider_api_key" in source and "resolve_secret_env" not in source
