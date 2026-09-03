"""秘書單次問答（ADR-013）：把儀表板對話框那條管線包成一個同步呼叫。

Web 的 `/api/v1/rag/chat` 是 SSE 串流，只適合瀏覽器。手機（Telegram）需要
「問一句、拿一整段答案」，因此這裡把同一條管線——**記憶區脈絡（ADR-012）
＋ RAG 檢索 ＋ LLM**——收斂成 :func:`ask_secretary`。

契約：

- **同一條管線**：檢索沿用 ``rag.router._retrieve_citations``（worker 或
  in_process 由設定決定），記憶區沿用 ``core.secretary_memory.memory_context``；
  不另立一套規則，答案來源與網頁一致。
- **絕不在模組層 import rag**：主服務 import ``core.server`` 不得載入
  chromadb／fastembed／rank_bm25／jieba（ADR-009 契約），所有 rag import
  都在函式內。
- **不會卡住呼叫端**：檢索與 LLM 各有逾時；任何一段失敗都降級為「照常回答
  但沒有文件脈絡」或回傳帶 ``error`` 的收據，呼叫端永遠拿得到東西。
- **收據誠實**：回傳值說明有沒有用到記憶區（幾筆、幾個字）、有沒有檢索到
  文件（幾則引用）、用了哪個 provider／model。
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from core.config import get_config

logger = logging.getLogger("OmniContext.SecretaryAsk")

DEFAULT_ANSWER_TIMEOUT_SECONDS = 120
MAX_QUESTION_CHARS = 2000

ASK_CLAIM_BOUNDARY = (
    "答案由所選 LLM 依本機記憶區與知識庫切片生成；引用列出的是被檢索到的檔案，"
    "不代表答案的每一句都有出處。"
)


class AskRejected(ValueError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


def _run_async(coro: Any, timeout: float) -> Any:
    """在沒有事件迴圈的執行緒（如 Telegram poller）跑 async 串流。

    若呼叫端本身已在事件迴圈裡（例如 FastAPI 端點），改丟到獨立執行緒跑，
    避免 ``asyncio.run`` 在既有迴圈中拋錯。
    """

    async def _with_timeout() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_with_timeout())).result()


class _RetrievalRequest:
    """`rag.router._retrieve_citations` 只讀這四個欄位；用最小殼避免重寫檢索邏輯。"""

    def __init__(self, strategy: str | None, top_k: int | None, alpha: float | None, threshold: float):
        self.retrieval_strategy = strategy
        self.top_k = top_k
        self.hybrid_alpha = alpha
        self.score_threshold = threshold


def _answer_timeout(cfg: Any) -> int:
    try:
        value = int(cfg.get("secretary_ask.timeout_seconds", DEFAULT_ANSWER_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_ANSWER_TIMEOUT_SECONDS
    return max(10, min(value, 600))


def ask_secretary(
    question: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    enable_rag: bool = True,
    top_k: int | None = None,
    retrieval_strategy: str | None = None,
    cfg: Any | None = None,
    timeout_seconds: int | None = None,
    memory: dict[str, Any] | None = None,
    gateway: Any | None = None,
) -> dict[str, Any]:
    """問一句、拿一整段答案；永遠回傳收據，不對呼叫端拋 LLM 例外。"""
    cfg = cfg or get_config()
    question = (question or "").strip()
    if not question:
        raise AskRejected("empty_question", "問題不可為空")
    if len(question) > MAX_QUESTION_CHARS:
        raise AskRejected("question_too_long", f"問題不可超過 {MAX_QUESTION_CHARS} 字")

    started = time.perf_counter()
    receipt: dict[str, Any] = {
        "answer": "",
        "citations": [],
        "memory": {"included": False, "reason": "not_requested"},
        "provider": (provider or cfg.get("rag.active_provider", "ollama")),
        "model": model,
        "rag_used": False,
        "error": None,
        "claim_boundary": ASK_CLAIM_BOUNDARY,
    }

    # 1. 記憶區脈絡（ADR-012）：與網頁對話注入的完全相同
    memory_text = ""
    if memory is not None:
        memory_text = str(memory.get("text") or "")
        receipt["memory"] = memory.get("receipt") or {"included": bool(memory_text)}
    else:
        try:
            from core.secretary_memory import chat_context_enabled, memory_context

            if chat_context_enabled(cfg):
                built = memory_context(cfg=cfg)
                memory_text = built["text"]
                receipt["memory"] = built["receipt"]
            else:
                receipt["memory"] = {"included": False, "reason": "disabled"}
        except Exception as exc:  # noqa: BLE001 — 記憶區故障不得中止問答
            logger.warning("memory context unavailable for ask: %s", type(exc).__name__)
            receipt["memory"] = {"included": False, "reason": f"error:{type(exc).__name__}"}

    # 2. 知識庫檢索（沿用與網頁同一條路徑）
    context_text = ""
    if enable_rag:
        try:
            from rag.retrieval.context import format_context_prompt
            from rag.router import _retrieve_citations

            citations = _retrieve_citations(
                question,
                _RetrievalRequest(retrieval_strategy, top_k, None, 0.0),
            )
            context_text = format_context_prompt(citations)
            receipt["rag_used"] = bool(citations)
            receipt["citations"] = [
                {
                    "index": getattr(c, "index", None),
                    "filename": getattr(c, "filename", None) or getattr(c, "title", None),
                    "file_path": getattr(c, "file_path", None),
                    "score": getattr(c, "score", None),
                }
                for c in citations
            ]
        except Exception as exc:  # noqa: BLE001 — 檢索失敗照常回答
            logger.warning("retrieval unavailable for ask: %s", type(exc).__name__)
            receipt["citations"] = []
            receipt["rag_used"] = False
            receipt["retrieval_error"] = type(exc).__name__

    # 3. LLM：把串流收成完整字串
    from rag.config import rag_settings

    prompt_parts = [str(rag_settings.DEFAULT_SYSTEM_PROMPT)]
    if memory_text:
        prompt_parts.append(memory_text)
    if context_text and receipt["rag_used"]:
        prompt_parts.append(context_text)
    system_prompt = "\n\n".join(prompt_parts)

    if gateway is None:
        from rag.llm_gateway import llm_gateway as gateway

    async def _collect() -> str:
        chunks: list[str] = []
        async for token in gateway.stream_chat(
            messages=[{"role": "user", "content": question}],
            system_prompt=system_prompt,
            provider=provider,
            model=model,
        ):
            chunks.append(token)
        return "".join(chunks)

    timeout = timeout_seconds or _answer_timeout(cfg)
    try:
        receipt["answer"] = _run_async(_collect(), timeout=timeout).strip()
    except asyncio.TimeoutError:
        receipt["error"] = "timeout"
        receipt["answer"] = f"（{timeout} 秒內沒有得到完整回答；請稍後再問，或改用本機 Ollama。）"
    except Exception as exc:  # noqa: BLE001 — provider 錯誤如實轉成可讀訊息
        logger.error("ask_secretary LLM call failed: %s", exc, exc_info=True)
        receipt["error"] = type(exc).__name__
        receipt["answer"] = f"（回答失敗：{type(exc).__name__}；詳見本機服務日誌。）"

    if not receipt["answer"]:
        receipt["answer"] = "（模型沒有回覆內容。）"
    receipt["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return receipt
