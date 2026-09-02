"""RAG 對話串流的契約：雲端 provider 可用，且介面永遠不會卡在「回覆中」。

這組測試鎖住兩個曾經同時失效的契約：

1. **金鑰解析必須取 ``.value``**：``resolve_secret_env`` 回傳的是
   ``SecretResolution`` 物件（恆為真值），直接使用會讓「未設定金鑰」判斷
   失效，並把含金鑰的物件 repr 帶進請求 URL。
2. **SSE 一定送出 ``done``**：瀏覽器只靠 ``done`` 解除「回覆中」狀態，
   因此檢索或 LLM 無論如何失敗，都必須收尾。
"""

import asyncio
import json
from pathlib import Path

import pytest

from rag import llm_gateway as gateway_module
from rag.llm_gateway import _resolve_api_key


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


# ---- 契約 1：金鑰解析 ----


def test_api_key_resolution_returns_plain_string(monkeypatch):
    monkeypatch.setattr(gateway_module, "get_config", lambda: DictConfig())
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-key")
    key = _resolve_api_key("gemini", "GEMINI_API_KEY")
    assert key == "AIza-test-key"
    assert isinstance(key, str)


def test_missing_api_key_is_falsy_not_an_object(monkeypatch):
    """SecretResolution 物件恆為真值；沒有金鑰時必須解析成空字串。"""
    monkeypatch.setattr(gateway_module, "get_config", lambda: DictConfig())
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    key = _resolve_api_key("gemini", "GEMINI_API_KEY", aliases=("GOOGLE_API_KEY",))
    assert key == ""
    assert not key  # 這一行就是當初壞掉的判斷


def test_api_key_env_name_follows_config(monkeypatch):
    monkeypatch.setattr(
        gateway_module,
        "get_config",
        lambda: DictConfig({"synthesizer": {"gemini": {"api_key_env": "MY_CUSTOM_KEY"}}}),
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("MY_CUSTOM_KEY", "from-custom-env")
    assert _resolve_api_key("gemini", "GEMINI_API_KEY") == "from-custom-env"


def test_gemini_alias_google_api_key(monkeypatch):
    monkeypatch.setattr(gateway_module, "get_config", lambda: DictConfig())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "alias-key")
    assert _resolve_api_key("gemini", "GEMINI_API_KEY", aliases=("GOOGLE_API_KEY",)) == "alias-key"


def test_secret_never_appears_in_request_url():
    """金鑰只能走 header：URL 會進 log 與錯誤訊息，不得含 secret。"""
    source = Path(__file__).resolve().parents[1] / "rag" / "llm_gateway.py"
    text = source.read_text(encoding="utf-8")
    assert "key={api_key}" not in text
    assert "x-goog-api-key" in text
    # 所有 provider 都必須取 .value（不得直接使用 SecretResolution 物件）
    assert "resolve_secret_env(" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("api_key = resolve_secret_env("):
            assert stripped.endswith(".value"), stripped


def test_no_bare_secret_resolution_in_rag_package():
    rag_dir = Path(__file__).resolve().parents[1] / "rag"
    offenders = []
    for path in rag_dir.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if "resolve_secret_env(" in stripped and "def " not in stripped and "import" not in stripped:
                if ".value" not in stripped:
                    offenders.append(f"{path.name}:{number}")
    assert offenders == [], f"這些呼叫忘了取 .value：{offenders}"


# ---- 契約 2：SSE 一定送出 done ----


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _Req:
    def __init__(self, enable_rag=False):
        self.messages = [_Msg("user", "測試問題")]
        self.enable_rag = enable_rag
        self.custom_system_prompt = None
        self.provider = "gemini"
        self.model = None
        self.retrieval_strategy = "hybrid_rrf"
        self.top_k = 3
        self.hybrid_alpha = 0.5
        self.score_threshold = 0.0


def _collect(req):
    from rag.router import chat_stream

    async def run():
        response = await chat_stream(req)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    return asyncio.run(run())


def _events(payload):
    return [line[len("event: "):] for line in payload.splitlines() if line.startswith("event: ")]


def test_stream_emits_done_even_when_llm_raises(monkeypatch):
    async def exploding_stream(**kwargs):
        raise RuntimeError("provider blew up")
        yield  # pragma: no cover — 讓函式成為 async generator

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", exploding_stream)
    payload = _collect(_Req())
    assert _events(payload)[-1] == "done"
    assert "對話串流中止" in payload


def test_stream_emits_done_on_normal_completion(monkeypatch):
    async def ok_stream(**kwargs):
        yield "你好"

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", ok_stream)
    payload = _collect(_Req())
    events = _events(payload)
    assert events[0] == "status"  # 立刻回位元組，避免被誤判為沒回應
    assert "message" in events and events[-1] == "done"
    assert "你好" in payload


def test_retrieval_timeout_degrades_but_still_answers(monkeypatch):
    """大型索引檢索卡住時：不使用文件脈絡，但仍要回答並收尾。"""
    import rag.router as router_module

    monkeypatch.setattr(router_module, "RETRIEVAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "in_process")

    class _StuckRegistry:
        def retrieve(self, **kwargs):
            import time

            time.sleep(5)
            return []

        def format_context_prompt(self, citations):
            return ""

    import rag.retrieval.registry as registry_module

    monkeypatch.setattr(registry_module, "retriever_registry", _StuckRegistry())

    captured = {}

    async def ok_stream(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        yield "已回答"

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", ok_stream)
    payload = _collect(_Req(enable_rag=True))
    assert _events(payload)[-1] == "done"
    assert "已回答" in payload
    # 逾時的檢索不得把文件切片或逾時說明混進 system prompt
    prompt = captured.get("system_prompt") or ""
    assert "參考知識庫文件切片" not in prompt
    assert "秒未完成" not in prompt


def test_retrieval_failure_does_not_abort_the_answer(monkeypatch):
    class _BrokenRegistry:
        def retrieve(self, **kwargs):
            raise OSError("index unavailable")

        def format_context_prompt(self, citations):
            return ""

    import rag.retrieval.registry as registry_module
    import rag.router as router_module

    monkeypatch.setattr(router_module, "retrieval_mode", lambda: "in_process")
    monkeypatch.setattr(registry_module, "retriever_registry", _BrokenRegistry())

    async def ok_stream(**kwargs):
        yield "仍然回答"

    monkeypatch.setattr(gateway_module.llm_gateway, "stream_chat", ok_stream)
    payload = _collect(_Req(enable_rag=True))
    assert _events(payload)[-1] == "done"
    assert "仍然回答" in payload
