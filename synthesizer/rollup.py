"""P5-R5 週／月報 rollup：把既有的每日摘要 reduce 成一份期間報告。

資料來源是 ``daily_summaries`` 表中「單日」摘要（``YYYY-MM-DD`` label），
不回頭重算原始事件；期間內缺摘要的日期如實列出，不得推測。統計數字
（commit／AI turn／檔案異動／視窗分鐘）一律直接取自資料庫 COUNT，不經
LLM。LLM reduce 失敗或回傳備援報告時回退 deterministic 拼接，payload
如實標記 ``llm_used=false``。輸出檔只寫入 ``exporters.reports_dir``，
絕不寫任何使用者 repo。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import func

from core.config import get_config
from core.database import get_db
from core.models import (
    AIPromptEvent,
    DailySummary,
    FileActivityEvent,
    GitActivityEvent,
    WindowEvent,
)
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now
from .prompt_templates import ROLLUP_SYNTHESIS_SYSTEM, ROLLUP_SYNTHESIS_USER

logger = logging.getLogger("OmniContext.Rollup")

ROLLUP_KINDS = ("weekly", "monthly")
_KIND_LABELS = {"weekly": "週報", "monthly": "月報"}
# 每日摘要餵入 LLM 的截斷上限；deterministic 拼接用較短節錄。
_LLM_DAY_CHARS = 4000
_DIGEST_DAY_CHARS = 1200
# LLMClient 失敗時回傳的本機備援報告以此開頭；rollup 視同 LLM 不可用。
_LLM_FALLBACK_PREFIX = "# ⚠️"

ROLLUP_CLAIM_BOUNDARY = (
    "Rollup 只彙整期間內已存在的每日摘要與資料庫統計；缺摘要的日期如實"
    "留空，不代表當日沒有工作，也不重算原始事件。"
)


def rollup_period(kind: str, now: Optional[datetime] = None) -> tuple[date, date, str]:
    """回傳最近一個「已完整結束」的期間（避免彙整進行中的週／月）。"""
    now = now or get_local_now()
    today = now.date()
    if kind == "weekly":
        start = today - timedelta(days=today.weekday() + 7)  # 上一個完整 ISO 週的週一
        end = start + timedelta(days=6)
        iso_year, iso_week, _ = start.isocalendar()
        return start, end, f"{iso_year}-W{iso_week:02d}"
    if kind == "monthly":
        first_of_this_month = today.replace(day=1)
        end = first_of_this_month - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, f"{start.year}-{start.month:02d}"
    raise ValueError(f"Unknown rollup kind: {kind}")


def _dates_in_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _range_stats(database: Any, start: date, end: date) -> dict[str, Any]:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)
    with database.session_scope() as session:
        git_commits = (
            session.query(func.count(GitActivityEvent.id))
            .filter(GitActivityEvent.timestamp >= start_dt, GitActivityEvent.timestamp <= end_dt)
            .scalar()
        ) or 0
        ai_turns = (
            session.query(func.count(AIPromptEvent.id))
            .filter(
                AIPromptEvent.timestamp >= start_dt,
                AIPromptEvent.timestamp <= end_dt,
                AIPromptEvent.turn_key.isnot(None),
            )
            .scalar()
        ) or 0
        file_events = (
            session.query(func.count(FileActivityEvent.id))
            .filter(
                FileActivityEvent.timestamp >= start_dt,
                FileActivityEvent.timestamp <= end_dt,
            )
            .scalar()
        ) or 0
        window_seconds = (
            session.query(func.coalesce(func.sum(WindowEvent.duration_seconds), 0.0))
            .filter(WindowEvent.start_time >= start_dt, WindowEvent.start_time <= end_dt)
            .scalar()
        ) or 0.0
    return {
        "git_commits": int(git_commits),
        "ai_turns": int(ai_turns),
        "file_events": int(file_events),
        "window_minutes": round(float(window_seconds) / 60.0, 1),
    }


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（截斷，原 {len(text)} 字）"


def _deterministic_digest(rows: list[dict[str, str]]) -> str:
    sections = []
    for row in rows:
        sections.append(f"### {row['date_str']}\n\n{_clip(row['markdown'], _DIGEST_DAY_CHARS)}")
    return "\n\n".join(sections)


def _llm_reduce(
    kind: str,
    period_label: str,
    rows: list[dict[str, str]],
    missing: list[str],
    llm_generate: Optional[Callable[[str, str], str]],
) -> Optional[str]:
    """回傳 LLM 彙整結果；不可用或回傳備援報告時回 None（呼叫端回退）。"""
    if llm_generate is None:
        try:
            from .llm_client import LLMClient

            llm_generate = LLMClient().generate
        except Exception:  # noqa: BLE001 — LLM 不可用即回退 deterministic
            return None
    daily_sections = "\n\n".join(
        f"【{row['date_str']}】\n{_clip(row['markdown'], _LLM_DAY_CHARS)}" for row in rows
    )
    kind_label = _KIND_LABELS[kind]
    try:
        reply = llm_generate(
            ROLLUP_SYNTHESIS_SYSTEM.format(kind_label=kind_label),
            ROLLUP_SYNTHESIS_USER.format(
                period_label=period_label,
                kind_label=kind_label,
                days_present=", ".join(row["date_str"] for row in rows),
                days_missing=", ".join(missing) if missing else "（無）",
                daily_sections=daily_sections,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rollup LLM reduce failed: %s", type(exc).__name__)
        return None
    reply = str(reply or "").strip()
    if not reply or reply.startswith(_LLM_FALLBACK_PREFIX):
        return None
    return reply


def build_report_rollup(
    kind: str,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: Optional[datetime] = None,
    llm_generate: Optional[Callable[[str, str], str]] = None,
) -> dict[str, Any]:
    if kind not in ROLLUP_KINDS:
        raise ValueError(f"Unknown rollup kind: {kind}")
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()

    start, end, label = rollup_period(kind, now)
    wanted = [item.isoformat() for item in _dates_in_range(start, end)]
    with database.session_scope() as session:
        found = (
            session.query(DailySummary)
            .filter(DailySummary.date_str.in_(wanted))
            .order_by(DailySummary.date_str.asc())
            .all()
        )
        rows = [
            {"date_str": row.date_str, "markdown": row.raw_markdown or ""}
            for row in found
        ]
    present = {row["date_str"] for row in rows}
    missing = [day for day in wanted if day not in present]

    payload: dict[str, Any] = {
        "kind": kind,
        "period_label": label,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "days_total": len(wanted),
        "days_with_summary": len(rows),
        "days_missing": len(missing),
        "llm_used": False,
        "output_path": None,
        "claim_boundary": ROLLUP_CLAIM_BOUNDARY,
    }
    if not rows:
        payload["note"] = "期間內沒有任何單日摘要，未產生報告檔"
        return payload

    stats = _range_stats(database, start, end)
    payload.update(stats)

    body = _llm_reduce(kind, label, rows, missing, llm_generate)
    if body is not None:
        payload["llm_used"] = True
    else:
        body = "## 各日摘要節錄（deterministic 回退）\n\n" + _deterministic_digest(rows)

    kind_label = _KIND_LABELS[kind]
    header_lines = [
        f"# 📚 {label} {kind_label}回顧（{start.isoformat()} ~ {end.isoformat()}）",
        f"> 產生時間：{now.strftime('%Y-%m-%d %H:%M')}；"
        f"來源：{len(rows)}/{len(wanted)} 日的每日摘要；"
        f"LLM 彙整：{'是' if payload['llm_used'] else '否（deterministic 回退）'}",
        f"> {ROLLUP_CLAIM_BOUNDARY}",
        "",
        "## 📊 期間統計（直接取自本機資料庫）",
        f"- Git commits：{stats['git_commits']}",
        f"- AI 互動 turns：{stats['ai_turns']}",
        f"- 檔案異動事件：{stats['file_events']}",
        f"- 視窗前景分鐘：{stats['window_minutes']}（不代表生產力）",
    ]
    if missing:
        header_lines.append(
            f"- 缺每日摘要的日期：{', '.join(missing)}（如實留空，不推測）"
        )
    markdown = "\n".join(header_lines) + "\n\n" + body + "\n"

    reports_dir = resolve_runtime_path(cfg.get("exporters.reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    prefix = "Weekly_Rollup" if kind == "weekly" else "Monthly_Rollup"
    output_path = reports_dir / f"{prefix}_{label}.md"
    output_path.write_text(markdown, encoding="utf-8")

    payload["output_path"] = str(output_path)
    payload["report_chars"] = len(markdown)
    return payload
