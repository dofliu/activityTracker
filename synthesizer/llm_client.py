import logging
import time
from typing import Any, Dict, Optional
from core.config import get_config
from core.secret_resolver import resolve_secret_env

logger = logging.getLogger("OmniContext.LLMClient")

_PROVIDER_KEY_ENVS = {
    "gemini": ("synthesizer.gemini.api_key_env", "GEMINI_API_KEY"),
    "anthropic": ("synthesizer.anthropic.api_key_env", "ANTHROPIC_API_KEY"),
    "openai": ("synthesizer.openai.api_key_env", "OPENAI_API_KEY"),
}


def diagnose_provider(provider: Optional[str] = None, *, generate_test: bool = True) -> Dict[str, Any]:
    """`python main.py llm-test`：回報 provider 連線與設定狀態，不輸出金鑰。"""
    cfg = get_config()
    provider = (provider or cfg.get("synthesizer.provider", "gemini") or "gemini").lower()
    report: Dict[str, Any] = {"provider": provider, "configured_default_provider": cfg.get("synthesizer.provider", "gemini")}

    ready = False
    if provider == "ollama":
        base_url = str(cfg.get("synthesizer.ollama.base_url", "http://localhost:11434")).rstrip("/")
        model = str(cfg.get("synthesizer.ollama.model", "llama3.1:8b"))
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
                f"base_url 正確（目前 {base_url}；Windows 預設 http://localhost:11434），"
                "以及防火牆未擋 11434。"
            )
    else:
        cfg_key, default_env = _PROVIDER_KEY_ENVS.get(provider, (None, None))
        if cfg_key is None:
            report["error"] = f"未知 provider：{provider}"
            return report
        env_name = str(cfg.get(cfg_key, default_env))
        resolution = resolve_secret_env(env_name, aliases=("GOOGLE_API_KEY",) if provider == "gemini" else ())
        report["api_key_env"] = env_name
        report["api_key_configured"] = bool(resolution.value)
        report["api_key_source"] = resolution.source if resolution.value else None
        report["model"] = cfg.get(f"synthesizer.{provider}.model")
        if not resolution.value:
            report["hint"] = f"在環境變數 {env_name} 設定金鑰後按監控配置頁「重新檢查」"
        ready = bool(resolution.value)

    if generate_test and ready:
        started = time.perf_counter()
        try:
            reply = LLMClient(provider).generate(
                "你是連線測試助手。", "請只回覆兩個字：OK"
            )
            latency = round(time.perf_counter() - started, 2)
            ok = bool(reply) and not str(reply).startswith("# ⚠️")
            report["generation_test"] = {
                "ok": ok,
                "latency_seconds": latency,
                "reply_snippet": str(reply)[:80],
            }
        except Exception as exc:  # noqa: BLE001
            report["generation_test"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
    elif generate_test:
        report["generation_test"] = {"ok": False, "skipped": "provider_not_ready"}
    return report


class LLMClient:
    def __init__(self, provider: Optional[str] = None):
        cfg = get_config()
        self.provider = provider or cfg.get("synthesizer.provider", "gemini")
        self.cfg = cfg

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """根據設定調用相應的 LLM 供應商生成總結"""
        provider = self.provider.lower()

        try:
            if provider == "gemini":
                return self._call_gemini(system_prompt, user_prompt)
            elif provider == "anthropic":
                return self._call_anthropic(system_prompt, user_prompt)
            elif provider == "openai":
                return self._call_openai(system_prompt, user_prompt)
            elif provider == "ollama":
                return self._call_ollama(system_prompt, user_prompt)
            else:
                logger.warning(f"Unknown provider '{provider}', falling back to Gemini.")
                return self._call_gemini(system_prompt, user_prompt)
        except Exception as e:
            logger.error(f"Error invoking LLM provider '{provider}': {e}", exc_info=True)
            # 若 API 呼叫失敗（如尚未設定 Key），提供本機結構化備援報告，確保系統不中斷
            return self._generate_fallback_summary(user_prompt, str(e))

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        api_key_env = self.cfg.get("synthesizer.gemini.api_key_env", "GEMINI_API_KEY")
        api_key = resolve_secret_env(api_key_env, aliases=("GOOGLE_API_KEY",)).value
        model_name = self.cfg.get("synthesizer.gemini.model", "gemini-2.5-flash")

        if not api_key:
            raise ValueError(f"Gemini API key not found in environment variable '{api_key_env}'")

        try:
            # 優先嘗試 google-genai 新版 SDK
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model_name,
                contents=f"{system_prompt}\n\n{user_prompt}"
            )
            return response.text
        except ImportError:
            # 備援嘗試 google.generativeai 舊版 SDK
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt
            )
            response = model.generate_content(user_prompt)
            return response.text

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic
        api_key_env = self.cfg.get("synthesizer.anthropic.api_key_env", "ANTHROPIC_API_KEY")
        api_key = resolve_secret_env(api_key_env).value
        model_name = self.cfg.get("synthesizer.anthropic.model", "claude-3-5-sonnet-20241022")

        if not api_key:
            raise ValueError(f"Anthropic API key not found in environment variable '{api_key_env}'")

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model_name,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return message.content[0].text

    def _call_openai(self, system_prompt: str, user_prompt: str) -> str:
        from openai import OpenAI
        api_key_env = self.cfg.get("synthesizer.openai.api_key_env", "OPENAI_API_KEY")
        api_key = resolve_secret_env(api_key_env).value
        model_name = self.cfg.get("synthesizer.openai.model", "gpt-4o")

        if not api_key:
            raise ValueError(f"OpenAI API key not found in environment variable '{api_key_env}'")

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content

    def _call_ollama(self, system_prompt: str, user_prompt: str) -> str:
        import requests
        base_url = self.cfg.get("synthesizer.ollama.base_url", "http://localhost:11434")
        model = self.cfg.get("synthesizer.ollama.model", "llama3.1:8b")

        url = f"{base_url.rstrip('/')}/api/generate"
        payload = {
            "model": model,
            "prompt": f"<system>\n{system_prompt}\n</system>\n\n<user>\n{user_prompt}\n</user>",
            "stream": False
        }
        res = requests.post(url, json=payload, timeout=120)
        res.raise_for_status()
        return res.json().get("response", "")

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
        return f"""# ⚠️ [本機備援模式] 每日活動與工作日誌

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
