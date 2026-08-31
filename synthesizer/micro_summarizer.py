"""兩層增量摘要的 map 階段：把 checkpoint 時段壓成 ≤100 字本機微摘要。

- 由排程的 periodic checkpoint 之後順帶執行；provider 預設本機 Ollama，
  因此 map 階段零 API 成本。
- 任何失敗（Ollama 未啟動、逾時、LLMClient 回傳備援 markdown）→
  **靜默跳過、不落庫**；日報 reduce 對缺漏時段會回退原始節錄，
  所以本層永遠不會讓日報壞掉。
- 只保存壓縮後文字與非敏感統計（字元數、事件數），不保存原文。
- 同一時段重生成採 upsert（唯一鍵 period_start + period_end）。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from core.config import get_config
from core.database import get_db
from core.models import ActivityMicroSummary
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.MicroSummarizer")

MICRO_SYSTEM_PROMPT = (
    "你是工作日誌壓縮器。把輸入的活動紀錄壓成最多 100 字的繁體中文重點："
    "提到專案名稱、完成了什麼、關鍵決策與未完成事項。"
    "只輸出重點文字本身，不要任何前言、標題或條列符號。"
)
MAX_SUMMARY_CHARS = 600
COMPACT_CONTEXT_CHARS = 12000
FALLBACK_PREFIX = "# ⚠️"


def micro_summary_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("synthesizer.micro_summary.enabled", True))


def micro_summary_provider(cfg: Any | None = None) -> str:
    cfg = cfg or get_config()
    return str(cfg.get("synthesizer.micro_summary.provider", "ollama") or "ollama").lower()


def _timeout_seconds(cfg: Any) -> int:
    try:
        return min(300, max(15, int(cfg.get("synthesizer.micro_summary.timeout_seconds", 90))))
    except (TypeError, ValueError):
        return 90


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def build_compact_context(range_data: dict[str, Any]) -> tuple[str, int]:
    """精簡版時段脈絡（遠小於日報 context）；回傳 (文字, 事件數)。"""
    lines: list[str] = []
    event_count = 0

    ai_events = range_data.get("ai_events", [])
    for item in ai_events[:60]:
        event_count += 1
        clock = str(item.get("time", ""))[11:16]
        tag = f"[{item['tag']}]" if item.get("tag") else ""
        line = f"- {clock} [{item.get('platform', '')}]{tag} 問:{_clip(item.get('prompt'), 120)}"
        response = str(item.get("response") or "").strip()
        if len(response) > 10:
            line += f" 答:{_clip(response, 100)}"
        lines.append(line)
    if len(ai_events) > 60:
        event_count += len(ai_events) - 60
        lines.append(f"- …另有 {len(ai_events) - 60} 筆 AI 互動")

    git_events = range_data.get("git_events", [])
    for item in git_events[:30]:
        event_count += 1
        clock = str(item.get("time", ""))[11:16]
        lines.append(f"- {clock} commit[{item.get('repo', '')}] {_clip(item.get('message'), 80)}")

    file_events = range_data.get("file_events", [])
    if file_events:
        event_count += len(file_events)
        per_project: dict[str, int] = {}
        for item in file_events:
            key = item.get("project") or "(未歸戶)"
            per_project[key] = per_project.get(key, 0) + 1
        parts = "、".join(f"{name} {count}" for name, count in sorted(per_project.items(), key=lambda x: -x[1])[:6])
        lines.append(f"- 檔案異動：{parts}")

    durations: dict[str, float] = {}
    for item in range_data.get("window_events", []):
        app = str(item.get("app") or "")
        if app and app.lower() not in ("idle", "unknown", "none"):
            durations[app] = durations.get(app, 0.0) + float(item.get("duration_sec") or 0.0)
    top_apps = [f"{app} {int(sec // 60)}分" for app, sec in sorted(durations.items(), key=lambda x: -x[1])[:3] if sec >= 60]
    if top_apps:
        lines.append(f"- 前景視窗：{'、'.join(top_apps)}")

    text = "\n".join(lines)
    if len(text) > COMPACT_CONTEXT_CHARS:
        text = text[:COMPACT_CONTEXT_CHARS] + "\n…（節錄截止）"
    return text, event_count


def _default_generate(provider: str, timeout_seconds: int) -> Callable[[str, str], str]:
    def _run(system_prompt: str, user_prompt: str) -> str:
        from synthesizer.llm_client import LLMClient

        client = LLMClient(provider)
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(client.generate, system_prompt, user_prompt).result(
                timeout=timeout_seconds
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    return _run


def generate_micro_summary(
    period_start: datetime,
    period_end: datetime,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    range_data: dict[str, Any] | None = None,
    llm_generate: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """產生並保存一段時段微摘要；任何失敗只回報狀態，不丟例外。"""
    cfg = cfg or get_config()
    if not micro_summary_enabled(cfg):
        return {"status": "disabled"}
    database = database or get_db()

    if range_data is None:
        from synthesizer.aggregator import fetch_events_in_range

        range_data = fetch_events_in_range(period_start, period_end)
    context, event_count = build_compact_context(range_data)
    if event_count == 0:
        return {"status": "skipped_empty_period"}

    provider = micro_summary_provider(cfg)
    model = str(cfg.get(f"synthesizer.{provider}.model", "") or "")
    generate = llm_generate or _default_generate(provider, _timeout_seconds(cfg))
    try:
        raw = generate(MICRO_SYSTEM_PROMPT, context)
    except Exception as exc:  # noqa: BLE001 — map 層失敗一律跳過
        logger.debug("Micro summary skipped (%s): %s", provider, type(exc).__name__)
        return {"status": "skipped_llm_unavailable", "reason": type(exc).__name__}

    if not raw or str(raw).lstrip().startswith(FALLBACK_PREFIX):
        # LLMClient 失敗時回傳備援 markdown，不得當成有效微摘要保存。
        return {"status": "skipped_llm_unavailable", "reason": "fallback_output"}
    text = " ".join(str(raw).split()).strip()[:MAX_SUMMARY_CHARS]
    if not text:
        return {"status": "skipped_llm_unavailable", "reason": "empty_output"}

    now = now or get_local_now()
    with database.session_scope() as session:
        row = (
            session.query(ActivityMicroSummary)
            .filter_by(period_start=period_start, period_end=period_end)
            .first()
        )
        if row is None:
            row = ActivityMicroSummary(
                period_start=period_start,
                period_end=period_end,
                created_at=now,
            )
            session.add(row)
        row.provider = provider
        row.model = model[:120] if model else None
        row.summary_text = text
        row.input_chars = len(context)
        row.event_count = event_count

    return {
        "status": "stored",
        "provider": provider,
        "chars": len(text),
        "input_chars": len(context),
        "event_count": event_count,
    }


def micro_summaries_for_range(
    range_start: datetime,
    range_end: datetime,
    *,
    database: Any | None = None,
) -> list[dict[str, Any]]:
    """回傳與範圍重疊的微摘要（依時間排序），供日報 reduce 使用。"""
    database = database or get_db()
    with database.session_scope() as session:
        rows = (
            session.query(ActivityMicroSummary)
            .filter(
                ActivityMicroSummary.period_start < range_end,
                ActivityMicroSummary.period_end > range_start,
            )
            .order_by(ActivityMicroSummary.period_start.asc())
            .all()
        )
        return [
            {
                "period_start": row.period_start,
                "period_end": row.period_end,
                "text": row.summary_text,
                "provider": row.provider,
                "event_count": row.event_count,
            }
            for row in rows
        ]
