import os
import json
import logging
from typing import Optional
from core.config import get_config

logger = logging.getLogger("OmniContext.LLMClient")


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
        api_key = os.environ.get(api_key_env) or os.environ.get("GOOGLE_API_KEY")
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
        api_key = os.environ.get(api_key_env)
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
        api_key = os.environ.get(api_key_env)
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
        """當尚未配置 API 金鑰時的本地結構化摘要"""
        return f"""# ⚠️ [本機備援模式] 每日活動與工作日誌

> [!NOTE]
> 偵測到尚未設定 LLM API 金鑰 (錯誤訊息: `{error_msg}`)。
> 請在環境變數或 `config.yaml` 中設定對應金鑰以啟用完整的 AI 深度洞察。
> 以下為基於本地數據庫的原始活動結構清單：

---

## 📋 本日原始活動上下文記錄

```text
{user_prompt[:2500]}
```

---
*Generated automatically by OmniContext Engine.*
"""
