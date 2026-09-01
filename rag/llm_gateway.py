import json
import logging
import httpx
from typing import AsyncGenerator, List, Dict, Any, Optional
from core.config import get_config
from core.secret_resolver import resolve_secret_env
from rag.config import rag_settings

logger = logging.getLogger("OmniContext.RAG.LLMGateway")


def _resolve_api_key(provider: str, default_env: str, aliases: tuple[str, ...] = ()) -> str:
    """回傳金鑰字串（沒有則空字串）。

    ``resolve_secret_env`` 回傳的是 SecretResolution 物件，必須取 ``.value``；
    直接使用物件會讓 ``if not api_key`` 永遠為真值判斷失效，且把物件 repr
    （內含金鑰）帶進請求。環境變數名稱沿用 config 的 ``api_key_env`` 設定，
    與 synthesizer 的行為一致。
    """
    env_name = str(get_config().get(f"synthesizer.{provider}.api_key_env", default_env) or default_env)
    return resolve_secret_env(env_name, aliases=aliases).value


class LLMGateway:
    def __init__(self):
        self.timeout = httpx.Timeout(90.0, connect=15.0)

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        cfg = get_config()
        prov = (provider or cfg.get("rag.active_provider", "ollama")).lower()

        if prov in ["openai", "gpt"]:
            async for token in self._stream_openai(messages, system_prompt, model):
                yield token
        elif prov in ["claude", "anthropic"]:
            async for token in self._stream_claude(messages, system_prompt, model):
                yield token
        elif prov in ["gemini", "google"]:
            async for token in self._stream_gemini(messages, system_prompt, model):
                yield token
        elif prov == "ollama":
            async for token in self._stream_ollama(messages, system_prompt, model):
                yield token
        else:
            yield f"[LLMGateway 錯誤]: 不支援的 LLM 提供者 '{prov}'"

    async def _stream_openai(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        model: Optional[str]
    ) -> AsyncGenerator[str, None]:
        cfg = get_config()
        api_key = _resolve_api_key("openai", "OPENAI_API_KEY")
        model_name = model or cfg.get("synthesizer.openai.model", "gpt-4o")

        if not api_key:
            yield "【尚未偵測到 OpenAI API Key，請在系統環境變數設定 OPENAI_API_KEY】"
            return

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key)
            stream = await client.chat.completions.create(
                model=model_name,
                messages=formatted_messages,
                stream=True,
                temperature=0.3
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield f"\n\n[OpenAI API 錯誤]: {str(e)}"

    async def _stream_claude(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        model: Optional[str]
    ) -> AsyncGenerator[str, None]:
        cfg = get_config()
        api_key = _resolve_api_key("anthropic", "ANTHROPIC_API_KEY")
        model_name = model or cfg.get("synthesizer.anthropic.model", "claude-3-5-sonnet-20241022")

        if not api_key:
            yield "【尚未偵測到 Anthropic Claude API Key，請在系統環境變數設定 ANTHROPIC_API_KEY】"
            return

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key)
            chat_msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ["user", "assistant"]]

            async with client.messages.stream(
                max_tokens=4096,
                system=system_prompt or "",
                messages=chat_msgs,
                model=model_name,
                temperature=0.3
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error(f"Claude streaming error: {e}")
            yield f"\n\n[Claude API 錯誤]: {str(e)}"

    async def _stream_gemini(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        model: Optional[str]
    ) -> AsyncGenerator[str, None]:
        cfg = get_config()
        api_key = _resolve_api_key("gemini", "GEMINI_API_KEY", aliases=("GOOGLE_API_KEY",))
        model_name = model or cfg.get("synthesizer.gemini.model", "gemini-3.7-flash")

        if not api_key:
            yield "【尚未偵測到 Google Gemini API Key，請在系統環境變數設定 GEMINI_API_KEY】"
            return

        try:
            # 金鑰只走 header：URL 會出現在錯誤訊息與各層 log，不放 secret。
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?alt=sse"
            headers = {"x-goog-api-key": api_key}

            contents = []
            for m in messages:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})

            payload: Dict[str, Any] = {"contents": contents}
            if system_prompt:
                payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
            payload["generationConfig"] = {"temperature": 0.3}

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        yield f"\n\n[Gemini API HTTP {response.status_code}]: {err_text.decode('utf-8', errors='replace')[:400]}"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            raw_data = line[6:].strip()
                            if raw_data == "[DONE]":
                                break
                            try:
                                json_chunk = json.loads(raw_data)
                                candidates = json_chunk.get("candidates", [])
                                if candidates and "content" in candidates[0]:
                                    parts = candidates[0]["content"].get("parts", [])
                                    for part in parts:
                                        if "text" in part:
                                            yield part["text"]
                            except Exception:
                                pass
        except Exception as e:
            logger.error(f"Gemini streaming error: {e}")
            yield f"\n\n[Gemini API 串流異常]: {str(e)}"

    async def _stream_ollama(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        model: Optional[str]
    ) -> AsyncGenerator[str, None]:
        cfg = get_config()
        host = str(cfg.get("synthesizer.ollama.base_url", "http://127.0.0.1:11434")).rstrip("/")
        model_name = model or cfg.get("rag.active_model", cfg.get("synthesizer.ollama.model", "llama3.2:latest"))

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{host}/api/chat",
                    json={"model": model_name, "messages": formatted_messages, "stream": True}
                ) as response:
                    if response.status_code != 200:
                        yield f"\n\n[Ollama 服務異常: HTTP {response.status_code}] 請確認本機 Ollama 服務已啟動 ({host})"
                        return

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            if "message" in chunk and "content" in chunk["message"]:
                                yield chunk["message"]["content"]
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Ollama streaming error: {e}")
            yield f"\n\n[Ollama 連線錯誤: {str(e)}] 請確認已開啟 Ollama (預設 {host})"


llm_gateway = LLMGateway()
