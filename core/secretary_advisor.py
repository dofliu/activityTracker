"""P5-R1 LLM advisory 層：對 deterministic proposals 做唯讀註解（ADR-008 階段 1）。

嚴格 annotate-only 契約：

- **不得**新增、刪除、重排 proposals，也不得修改任何 deterministic 欄位；
  LLM 只能為既有 `proposal_id` 附加 `llm_note` 與 `llm_priority_hint`，
  以及 envelope 層級的 `advisor.summary`。
- 預設關閉（`proactive_secretary.llm_advisor.enabled: false`）；關閉時
  輸出與 ADR-007 proposal-only 完全一致（僅多出 status=disabled 的
  `advisor` 標示欄位）。
- 預設 provider 為本機 Ollama。選擇 cloud provider 代表使用者同意將
  proposal 的白名單欄位（title / reason / suggested_action 等，不含
  prompt 全文、token 或本機路徑）送往該供應商——與 synthesizer 摘要的
  既有資料邊界相同；此時 envelope 的 `cloud_llm_used` 會如實轉為 true。
- 任何失敗（連線、逾時、非 JSON、schema 不符）→ 原樣回傳 deterministic
  結果並標記 `fallback_deterministic`；秘書功能永不因 LLM 不可用而中斷。
- 註解不落地：僅有程序內 TTL cache 避免重複呼叫，不寫入 SQLite。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable

from core.config import get_config
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.SecretaryAdvisor")

ADVISOR_CLAIM_BOUNDARY = (
    "LLM annotations are read-only advisory text over deterministic proposals; "
    "they cannot add, remove or execute anything, and are not persisted."
)

# proposal 只有這些欄位允許進入 prompt——白名單而非黑名單。
PROMPT_FIELDS = (
    "proposal_id",
    "proposal_type",
    "project_key",
    "title",
    "detail",
    "reason",
    "suggested_action",
    "priority",
    "age_days",
    "score",
    "same_project_pending",
)

MAX_NOTE_CHARS = 300
MAX_SUMMARY_CHARS = 600
_ALLOWED_PRIORITY_HINTS = {"high", "medium", "low"}
_DEFAULT_MODELS = {
    "ollama": "llama3.1:8b",
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-3-5-sonnet-20241022",
    "openai": "gpt-4o",
}

_SYSTEM_PROMPT = (
    "你是一位唯讀的個人工作分流顧問。輸入是一份由規則引擎產生的工作建議清單"
    "（JSON）。你的任務：\n"
    "1. 為每一項建議寫一句更聰明、更具體的繁體中文判斷提示（note，80 字內），"
    "幫助使用者決定先後與取捨；可指出項目之間的關聯。\n"
    "2. 對排序給出 priority_hint（high/medium/low）。\n"
    "3. 用 2-3 句寫一段今日整體 summary（150 字內）。\n"
    "限制：你沒有執行能力，不得建議執行任何系統指令、刪除資料或自動化操作；"
    "不得虛構清單以外的事項；只能引用輸入中出現的 proposal_id。\n"
    "輸出格式：只回傳一個 JSON 物件，不要其他文字：\n"
    '{"summary": "...", "annotations": [{"proposal_id": "...", "note": "...", '
    '"priority_hint": "high|medium|low"}]}'
)


def advisor_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    provider = str(
        cfg.get("proactive_secretary.llm_advisor.provider", "ollama") or "ollama"
    ).lower()

    def _clamped(key: str, default: int, low: int, high: int) -> int:
        try:
            return min(high, max(low, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(cfg.get("proactive_secretary.llm_advisor.enabled", False)),
        "provider": provider,
        "cloud": provider != "ollama",
        "model": str(
            cfg.get(
                f"synthesizer.{provider}.model",
                _DEFAULT_MODELS.get(provider, ""),
            )
        ),
        "timeout_seconds": _clamped(
            "proactive_secretary.llm_advisor.timeout_seconds", 20, 5, 120
        ),
        "cache_minutes": _clamped(
            "proactive_secretary.llm_advisor.cache_minutes", 10, 0, 240
        ),
    }


def _prompt_payload(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: item.get(key) for key in PROMPT_FIELDS if item.get(key) not in (None, "")}
        for item in proposals
    ]


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """從模型輸出擷取第一個 JSON 物件；容忍 code fence 與前後雜訊。"""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", str(text))
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _clean_text(value: Any, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()
    if not text:
        return None
    return text[:max_chars]


def _sanitize(
    parsed: dict[str, Any], valid_ids: set[str]
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """只保留合法 proposal_id 的註解；未知 id、超長與非法值一律丟棄。"""
    summary = _clean_text(parsed.get("summary"), MAX_SUMMARY_CHARS)
    annotations: dict[str, dict[str, Any]] = {}
    raw_items = parsed.get("annotations")
    if isinstance(raw_items, list):
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            proposal_id = str(raw.get("proposal_id") or "")
            if proposal_id not in valid_ids or proposal_id in annotations:
                continue
            note = _clean_text(raw.get("note"), MAX_NOTE_CHARS)
            hint = str(raw.get("priority_hint") or "").lower()
            entry: dict[str, Any] = {}
            if note:
                entry["llm_note"] = note
            if hint in _ALLOWED_PRIORITY_HINTS:
                entry["llm_priority_hint"] = hint
            if entry:
                annotations[proposal_id] = entry
    return summary, annotations


class _AdvisorCache:
    """程序內 TTL cache；不寫 SQLite，重啟即失效（與『不保存』一致）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: str | None = None
        self._expires_at: datetime | None = None
        self._value: tuple[str | None, dict[str, dict[str, Any]]] | None = None

    def get(self, key: str, now: datetime):
        with self._lock:
            if (
                self._key == key
                and self._value is not None
                and self._expires_at is not None
                and now < self._expires_at
            ):
                return self._value
            return None

    def put(self, key: str, value, now: datetime, ttl_minutes: int) -> None:
        if ttl_minutes <= 0:
            return
        with self._lock:
            self._key = key
            self._value = value
            self._expires_at = now + timedelta(minutes=ttl_minutes)

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._value = None
            self._expires_at = None


_cache = _AdvisorCache()


def _default_generate(provider: str, timeout_seconds: int) -> Callable[[str, str], str]:
    def _run(system_prompt: str, user_prompt: str) -> str:
        from synthesizer.llm_client import LLMClient

        client = LLMClient(provider)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.generate, system_prompt, user_prompt)
            return future.result(timeout=timeout_seconds)

    return _run


def annotate_action_proposals(
    result: dict[str, Any],
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    llm_generate: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """包裝 ``build_action_proposals`` 的輸出；永不改變 deterministic 內容。"""
    cfg = cfg or get_config()
    settings = advisor_settings(cfg)
    advisor: dict[str, Any] = {
        "enabled": settings["enabled"],
        "provider": settings["provider"] if settings["enabled"] else None,
        "model": settings["model"] if settings["enabled"] else None,
        "status": "disabled",
        "annotated": 0,
        "summary": None,
        "claim_boundary": ADVISOR_CLAIM_BOUNDARY,
    }
    result["advisor"] = advisor
    if not settings["enabled"]:
        return result
    proposals = result.get("proposals") or []
    if result.get("status") != "proposal_only" or not proposals:
        advisor["status"] = "skipped_no_proposals"
        return result

    now = now or get_local_now()
    payload = _prompt_payload(proposals)
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha256(
        f"{settings['provider']}|{settings['model']}|{payload_text}".encode("utf-8")
    ).hexdigest()
    valid_ids = {str(item.get("proposal_id")) for item in proposals}

    cached = _cache.get(cache_key, now)
    if cached is not None:
        summary, annotations = cached
        advisor["status"] = "cached"
    else:
        generate = llm_generate or _default_generate(
            settings["provider"], settings["timeout_seconds"]
        )
        try:
            raw = generate(_SYSTEM_PROMPT, payload_text)
        except Exception as exc:  # noqa: BLE001 — 含 timeout；失敗一律回退
            logger.warning("Secretary advisor unavailable: %s", type(exc).__name__)
            advisor["status"] = "fallback_deterministic"
            advisor["fallback_reason"] = type(exc).__name__
            return result
        parsed = _extract_json_object(raw)
        if parsed is None:
            advisor["status"] = "fallback_deterministic"
            advisor["fallback_reason"] = "invalid_json"
            return result
        summary, annotations = _sanitize(parsed, valid_ids)
        if not summary and not annotations:
            # 例如 LLMClient 的備援 markdown 夾帶了 payload 裡的 JSON 片段：
            # 解析得出物件但沒有任何可用註解，一律視為失敗且不得寫入 cache。
            advisor["status"] = "fallback_deterministic"
            advisor["fallback_reason"] = "no_usable_annotations"
            return result
        _cache.put(cache_key, (summary, annotations), now, settings["cache_minutes"])
        advisor["status"] = "annotated"

    for item in proposals:
        entry = annotations.get(str(item.get("proposal_id")))
        if entry:
            item.update(entry)
    advisor["summary"] = summary
    advisor["annotated"] = len(annotations)
    advisor["generated_at"] = now.isoformat(timespec="seconds")
    if settings["cloud"]:
        # 誠實旗標：cloud advisor 實際被使用時，envelope 不得再宣稱未用 cloud LLM。
        result["cloud_llm_used"] = True
    return result


def reset_advisor_cache() -> None:
    """測試用：清空程序內 cache。"""
    _cache.clear()
