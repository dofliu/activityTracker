"""OmniContext 唯一的 LLM client（TODO D2，ROADMAP §13 R1）。

2026-09-16 之前有兩套：`synthesizer/llm_client.py`（同步 `generate`，Ollama 走 `/api/generate`）
與 `rag/llm_gateway.py`（非同步串流，Ollama 走 `/api/chat`），讀同一組設定鍵，但預設模型
已經漂移（gemini-2.5 vs 3.7）、金鑰解析寫了四份。這裡把它們收成一個模組：

- **provider 名稱、預設模型、金鑰環境變數**各只定義一次（`DEFAULT_MODELS`／`KEY_ENVS`）。
- `LLMClient.generate(system, user)`：同步、回字串；失敗**回傳**備援 markdown（`[本機備援模式]`
  抬頭）而不是丟例外——日報永遠要能產生，這是既有契約（`synthesizer/aggregator.py`）。
- `LLMClient.stream_chat(messages, ...)`：非同步逐 token；失敗**yield 錯誤字串**
  （`[OpenAI API 錯誤]` 等抬頭）而不是丟例外——SSE 一定要收尾，這也是既有契約。
  兩種失敗抬頭都被 `core/meeting_transcripts.looks_like_llm_error` 與 `core/acceptance.py`
  當作「供應商錯誤」辨識，**不得更動字面**。
- Ollama 兩條路徑都走 `/api/chat`（訊息陣列＋system role），不再用 `<system>` 標籤拼字串。
- 金鑰一律 `resolve_secret_env(...).value`，且只放 header，不進 URL（2026-09-01 的教訓）。

不做的事：不重試、不快取、不做 provider fallback（呼叫端各自決定要不要退回規則版）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence

from core.config import get_config
from core.secret_resolver import resolve_secret_env

logger = logging.getLogger("OmniContext.LLMClient")

# ---- provider 名稱與預設（單一定義） ------------------------------------------

PROVIDER_ALIASES: Dict[str, str] = {"gpt": "openai", "claude": "anthropic", "google": "gemini"}
PROVIDERS: tuple[str, ...] = ("ollama", "gemini", "anthropic", "openai")
CLOUD_PROVIDERS: tuple[str, ...] = ("gemini", "anthropic", "openai")

# 與 config.example.yaml 的 synthesizer.<provider>.model 一致；改這裡就是改全 repo 的預設。
DEFAULT_MODELS: Dict[str, str] = {
    "ollama": "llama3.1:8b",
    "gemini": "gemini-3.7-flash",
    "anthropic": "claude-3-5-sonnet-20241022",
    "openai": "gpt-4o",
}
# provider → (預設環境變數名, 別名)。實際名稱可由 synthesizer.<provider>.api_key_env 覆寫。
KEY_ENVS: Dict[str, tuple[str, tuple[str, ...]]] = {
    "gemini": ("GEMINI_API_KEY", ("GOOGLE_API_KEY",)),
    "anthropic": ("ANTHROPIC_API_KEY", ()),
    "openai": ("OPENAI_API_KEY", ()),
}
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# 錯誤抬頭：下游（事實閘、驗收中心）靠這些字面辨識「供應商錯誤」，改字要一起改那邊。
FALLBACK_PREFIX = "# ⚠️ [本機備援模式]"


def normalize_provider(name: Optional[str], cfg: Any | None = None) -> str:
    """把 gpt／claude／google 等別名收成四個正式名稱；空值取 synthesizer.provider。"""
    cfg = cfg or get_config()
    raw = str(name or cfg.get("synthesizer.provider", "ollama") or "ollama").strip().lower()
    return PROVIDER_ALIASES.get(raw, raw)


def default_model(provider: str, cfg: Any | None = None) -> str:
    """該 provider 的模型名：設定檔優先，否則 DEFAULT_MODELS。"""
    cfg = cfg or get_config()
    provider = PROVIDER_ALIASES.get(provider, provider)
    return str(cfg.get(f"synthesizer.{provider}.model", DEFAULT_MODELS.get(provider, "")) or DEFAULT_MODELS.get(provider, ""))


def api_key_env_name(provider: str, cfg: Any | None = None) -> str:
    cfg = cfg or get_config()
    provider = PROVIDER_ALIASES.get(provider, provider)
    default_env, _aliases = KEY_ENVS.get(provider, ("", ()))
    return str(cfg.get(f"synthesizer.{provider}.api_key_env", default_env) or default_env)


def resolve_provider_api_key(provider: str, cfg: Any | None = None) -> str:
    """回傳金鑰字串（沒有則空字串）。

    ``resolve_secret_env`` 回傳的是 SecretResolution 物件，**必須取 ``.value``**；
    直接使用物件會讓 ``if not api_key`` 永遠為真值判斷失效，且把物件 repr（內含金鑰）
    帶進請求（2026-09-01 曾讓所有雲端 provider 的 RAG 對話全滅）。
    """
    provider = PROVIDER_ALIASES.get(provider, provider)
    _default_env, aliases = KEY_ENVS.get(provider, ("", ()))
    env_name = api_key_env_name(provider, cfg)
    if not env_name:
        return ""
    return resolve_secret_env(env_name, aliases=aliases).value or ""


def ollama_base_url(cfg: Any | None = None) -> str:
    cfg = cfg or get_config()
    return str(cfg.get("synthesizer.ollama.base_url", DEFAULT_OLLAMA_BASE_URL) or DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def _chat_messages(system_prompt: Optional[str], messages: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    formatted: List[Dict[str, str]] = []
    if system_prompt:
        formatted.append({"role": "system", "content": system_prompt})
    formatted.extend({"role": m["role"], "content": m["content"]} for m in messages)
    return formatted


# ---- 診斷（python main.py llm-test） --------------------------------------------


def diagnose_provider(provider: Optional[str] = None, *, generate_test: bool = True) -> Dict[str, Any]:
    """回報 provider 連線與設定狀態，不輸出金鑰。"""
    cfg = get_config()
    provider = normalize_provider(provider, cfg)
    report: Dict[str, Any] = {"provider": provider, "configured_default_provider": cfg.get("synthesizer.provider", "ollama")}

    ready = False
    if provider == "ollama":
        base_url = ollama_base_url(cfg)
        model = default_model("ollama", cfg)
        report.update({"base_url": base_url, "model": model})
        try:
            import requests

            tags = requests.get(f"{base_url}/api/tags", timeout=5)
            tags.raise_for_status()
            models = [str(m.get("name", "")) for m in tags.json().get("models", [])]
            report["reachable"] = True
            report["available_models"] = models[:25]
            report["model_installed"] = any(
                name == model or name.split(":")[0] == model.split(":")[0]
                for name in models
            )
            if not report["model_installed"]:
                report["hint"] = f"模型 `{model}` 不在本機清單中；執行 `ollama pull {model}` 或改設 synthesizer.ollama.model"
            ready = report["model_installed"]
        except Exception as exc:  # noqa: BLE001 — 診斷工具需回報所有失敗型態
            report["reachable"] = False
            report["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            report["hint"] = (
                "確認 Ollama 服務執行中（`ollama list` 可回應）、"
                f"base_url 正確（目前 {base_url}；Windows 預設 {DEFAULT_OLLAMA_BASE_URL}），"
                "以及防火牆未擋 11434。"
            )
    elif provider in KEY_ENVS:
        env_name = api_key_env_name(provider, cfg)
        _default_env, aliases = KEY_ENVS[provider]
        resolution = resolve_secret_env(env_name, aliases=aliases)
        report["api_key_env"] = env_name
        report["api_key_configured"] = bool(resolution.value)
        report["api_key_source"] = resolution.source if resolution.value else None
        report["model"] = default_model(provider, cfg)
        if not resolution.value:
            report["hint"] = f"在環境變數 {env_name} 設定金鑰後按監控配置頁「重新檢查」"
        ready = bool(resolution.value)
    else:
        report["error"] = f"未知 provider：{provider}"
        return report

    if generate_test and ready:
        started = time.perf_counter()
        try:
            reply = LLMClient(provider).generate("你是連線測試助手。", "請只回覆兩個字：OK")
            latency = round(time.perf_counter() - started, 2)
            ok = bool(reply) and not str(reply).startswith("# ⚠️")
            report["generation_test"] = {"ok": ok, "latency_seconds": latency, "reply_snippet": str(reply)[:80]}
        except Exception as exc:  # noqa: BLE001
            report["generation_test"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    elif generate_test:
        report["generation_test"] = {"ok": False, "skipped": "provider_not_ready"}
    return report


# ---- client ------------------------------------------------------------------------


class LLMClient:
    def __init__(self, provider: Optional[str] = None, cfg: Any | None = None):
        self.cfg = cfg or get_config()
        self.provider = normalize_provider(provider, self.cfg)
        self.stream_timeout_seconds = 90.0
        self.stream_connect_timeout_seconds = 15.0

    # ---- 同步：回字串，失敗回備援 markdown ------------------------------------

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """根據設定調用相應的 LLM 供應商；失敗時回傳本機備援報告，不丟例外。"""
        provider = self.provider
        try:
            if provider == "gemini":
                return self._call_gemini(system_prompt, user_prompt)
            if provider == "anthropic":
                return self._call_anthropic(system_prompt, user_prompt)
            if provider == "openai":
                return self._call_openai(system_prompt, user_prompt)
            if provider == "ollama":
                return self.ollama_chat(_chat_messages(system_prompt, [{"role": "user", "content": user_prompt}]))
            raise ValueError(f"Unknown LLM provider '{provider}'")
        except Exception as e:  # noqa: BLE001 — 若 API 呼叫失敗，提供本機結構化備援報告，確保系統不中斷
            logger.error(f"Error invoking LLM provider '{provider}': {e}", exc_info=True)
            return self._generate_fallback_summary(user_prompt, str(e))

    def _require_key(self, provider: str) -> str:
        api_key = resolve_provider_api_key(provider, self.cfg)
        if not api_key:
            label = {"gemini": "Gemini", "anthropic": "Anthropic", "openai": "OpenAI"}[provider]
            raise ValueError(f"{label} API key not found in environment variable '{api_key_env_name(provider, self.cfg)}'")
        return api_key

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        api_key = self._require_key("gemini")
        model_name = default_model("gemini", self.cfg)
        try:
            from google import genai  # 優先嘗試 google-genai 新版 SDK

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(model=model_name, contents=f"{system_prompt}\n\n{user_prompt}")
            return response.text
        except ImportError:
            import google.generativeai as genai  # 備援：舊版 SDK

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name=model_name, system_instruction=system_prompt)
            return model.generate_content(user_prompt).text

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        api_key = self._require_key("anthropic")
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=default_model("anthropic", self.cfg),
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def _call_openai(self, system_prompt: str, user_prompt: str) -> str:
        from openai import OpenAI

        api_key = self._require_key("openai")
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=default_model("openai", self.cfg),
            messages=_chat_messages(system_prompt, [{"role": "user", "content": user_prompt}]),
            temperature=0.3,
        )
        return response.choices[0].message.content

    def ollama_chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        timeout: float = 120,
    ) -> str:
        """同步的 Ollama `/api/chat`（非串流）。唯一一份 Ollama 同步實作，
        `core/semantic_index.py` 的 `omni ask` 也走這裡（它自己決定 loopback-only 的 base_url）。"""
        import requests

        payload: Dict[str, Any] = {
            "model": model or default_model("ollama", self.cfg),
            "messages": list(messages),
            "stream": False,
        }
        if options:
            payload["options"] = dict(options)
        res = requests.post(f"{(base_url or ollama_base_url(self.cfg)).rstrip('/')}/api/chat", json=payload, timeout=timeout)
        res.raise_for_status()
        return str((res.json().get("message") or {}).get("content") or "")

    def _generate_fallback_summary(self, user_prompt: str, error_msg: str) -> str:
        """LLM 呼叫失敗時的本地結構化備援；訊息必須如實區分失敗原因。"""
        error_text = str(error_msg or "")[:400]
        if "API key not found" in error_text:
            diagnosis = (
                f"偵測到尚未設定 LLM API 金鑰（provider: `{self.provider}`）。"
                "請在作業系統環境變數設定對應金鑰，並在 `config.yaml` 以 `api_key_env` 指定變數名稱。"
            )
        elif "token count exceeds" in error_text or "INVALID_ARGUMENT" in error_text:
            diagnosis = (
                f"LLM 呼叫被 provider 拒絕（provider: `{self.provider}`），"
                "通常是輸入內容超過模型 token 上限。系統已內建 prompt 節錄與總量上限，"
                "若仍發生可調低 `synthesizer.max_prompt_chars`。"
                f"\n> 原始錯誤：`{error_text}`"
            )
        else:
            diagnosis = (
                f"LLM 呼叫失敗（provider: `{self.provider}`）。"
                "可執行 `python main.py llm-test` 診斷連線與模型設定。"
                f"\n> 原始錯誤：`{error_text}`"
            )
        return f"""{FALLBACK_PREFIX} 每日活動與工作日誌

> [!NOTE]
> {diagnosis}
> 以下為基於本地數據庫的原始活動結構清單：

---

## 📋 本日原始活動上下文記錄

```text
{user_prompt[:2500]}
```

---
*Generated automatically by OmniContext Engine.*
"""

    # ---- 非同步串流：逐 token，失敗 yield 錯誤字串 --------------------------------

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """逐 token 串流。``provider``／``model`` 為空時用本 client 的 provider 與其預設模型。

        錯誤一律以字串 yield（不丟例外）：SSE 的呼叫端靠這一點保證一定送出 done。
        """
        prov = normalize_provider(provider, self.cfg) if provider else self.provider
        if prov == "openai":
            async for token in self._stream_openai(messages, system_prompt, model):
                yield token
        elif prov == "anthropic":
            async for token in self._stream_anthropic(messages, system_prompt, model):
                yield token
        elif prov == "gemini":
            async for token in self._stream_gemini(messages, system_prompt, model):
                yield token
        elif prov == "ollama":
            async for token in self._stream_ollama(messages, system_prompt, model):
                yield token
        else:
            yield f"[LLMGateway 錯誤]: 不支援的 LLM 提供者 '{prov}'"

    def _httpx_timeout(self):
        import httpx

        return httpx.Timeout(self.stream_timeout_seconds, connect=self.stream_connect_timeout_seconds)

    async def _stream_openai(self, messages, system_prompt, model) -> AsyncGenerator[str, None]:
        api_key = resolve_provider_api_key("openai", self.cfg)
        if not api_key:
            yield f"【尚未偵測到 OpenAI API Key，請在系統環境變數設定 {api_key_env_name('openai', self.cfg)}】"
            return
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)
            stream = await client.chat.completions.create(
                model=model or default_model("openai", self.cfg),
                messages=_chat_messages(system_prompt, messages),
                stream=True,
                temperature=0.3,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:  # noqa: BLE001 — 串流錯誤轉成可讀字串，呼叫端才能收尾
            logger.error(f"OpenAI streaming error: {e}")
            yield f"\n\n[OpenAI API 錯誤]: {str(e)}"

    async def _stream_anthropic(self, messages, system_prompt, model) -> AsyncGenerator[str, None]:
        api_key = resolve_provider_api_key("anthropic", self.cfg)
        if not api_key:
            yield f"【尚未偵測到 Anthropic Claude API Key，請在系統環境變數設定 {api_key_env_name('anthropic', self.cfg)}】"
            return
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key)
            chat_msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
            async with client.messages.stream(
                max_tokens=4096,
                system=system_prompt or "",
                messages=chat_msgs,
                model=model or default_model("anthropic", self.cfg),
                temperature=0.3,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:  # noqa: BLE001
            logger.error(f"Claude streaming error: {e}")
            yield f"\n\n[Claude API 錯誤]: {str(e)}"

    async def _stream_gemini(self, messages, system_prompt, model) -> AsyncGenerator[str, None]:
        import httpx

        api_key = resolve_provider_api_key("gemini", self.cfg)
        if not api_key:
            yield f"【尚未偵測到 Google Gemini API Key，請在系統環境變數設定 {api_key_env_name('gemini', self.cfg)}】"
            return
        model_name = model or default_model("gemini", self.cfg)
        try:
            # 金鑰只走 header：URL 會出現在錯誤訊息與各層 log，不放 secret。
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?alt=sse"
            headers = {"x-goog-api-key": api_key}
            contents = [
                {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                for m in messages
            ]
            payload: Dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": 0.3}}
            if system_prompt:
                payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

            async with httpx.AsyncClient(timeout=self._httpx_timeout()) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        yield f"\n\n[Gemini API HTTP {response.status_code}]: {err_text.decode('utf-8', errors='replace')[:400]}"
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw_data = line[6:].strip()
                        if raw_data == "[DONE]":
                            break
                        try:
                            candidates = json.loads(raw_data).get("candidates", [])
                        except ValueError:
                            continue
                        if candidates and "content" in candidates[0]:
                            for part in candidates[0]["content"].get("parts", []):
                                if "text" in part:
                                    yield part["text"]
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini streaming error: {e}")
            yield f"\n\n[Gemini API 串流異常]: {str(e)}"

    async def _stream_ollama(self, messages, system_prompt, model) -> AsyncGenerator[str, None]:
        import httpx

        host = ollama_base_url(self.cfg)
        model_name = model or default_model("ollama", self.cfg)
        try:
            async with httpx.AsyncClient(timeout=self._httpx_timeout()) as client:
                async with client.stream(
                    "POST",
                    f"{host}/api/chat",
                    json={"model": model_name, "messages": _chat_messages(system_prompt, messages), "stream": True},
                ) as response:
                    if response.status_code != 200:
                        yield f"\n\n[Ollama 服務異常: HTTP {response.status_code}] 請確認本機 Ollama 服務已啟動 ({host})"
                        return
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError:
                            continue
                        content = (chunk.get("message") or {}).get("content")
                        if content:
                            yield content
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama streaming error: {e}")
            yield f"\n\n[Ollama 連線錯誤: {str(e)}] 請確認已開啟 Ollama (預設 {host})"


# 串流呼叫端（rag/router、core/secretary_ask）共用這一個實例；provider 由呼叫端傳入。
llm_client = LLMClient()
