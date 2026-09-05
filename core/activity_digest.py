"""每日工作誌：把「採集器看到的一天」變成小秘書記得住的一則觀察。

使用者的原話是：在 Antigravity 下指令、在 OmniContext 介面測試、編修論文——
這些 OmniContext 其實都看到了，但**小秘書的大腦裡沒有留下任何一天的紀錄**。
今天的觀察（ADR-012）只來自早晨包收據，講的是秘書自己輸出了什麼（幾個 repo
需要 pull、STATUS 過期幾個），不是「你做了什麼」。所以問它「上週我在
uavMonitor 上做了什麼」，它答不出來。

這個模組補的就是那一段：對**已經存在的**資料做 reduce，寫成一則當日觀察。

設計上刻意的三件事：

- **不新增資料類別。** 「你問 AI 什麼」這件事，`activity_micro_summaries`
  已經在存了——checkpoint 時段由本機 LLM 把事件（含你的 prompt）壓成 ≤600 字
  摘要。本模組只是把當天的微摘要與可回溯計數 reduce 成一則筆記，因此
  [ADR-012](../docs/ADR-012-secretary-memory.md)「不存 prompt／response 原文」
  的邊界原封不動。
- **不呼叫 LLM。** 它是 reduce，不是重新生成：微摘要是既有的，計數是查詢。
  沒有微摘要（例如本機 LLM 沒開）就只寫計數，並如實說明只有計數。
- **只讀、可刪、會過期。** 寫進去的是 `observation`，沿用既有的 source_ref
  去重（每天每種一則）、TTL 與一鍵刪除。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from sqlalchemy import func

from core.config import get_config
from core.database import get_db
from core.models import (
    ActivityMicroSummary,
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
)
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.ActivityDigest")

DIGEST_CLAIM_BOUNDARY = (
    "只彙整採集器看到的活動與已保存的時段微摘要；沒被採集到的工作不代表沒做。"
    "本動作唯讀、不呼叫 LLM、不改任何既有資料，寫出的觀察可在記憶區一鍵刪除。"
)

MAX_DAYS_BACK = 7
DEFAULT_MAX_PROJECTS = 5
DEFAULT_MAX_HIGHLIGHTS = 4
MAX_BODY_CHARS = 900
MAX_HIGHLIGHT_CHARS = 160


def digest_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("proactive_secretary.daily_digest.enabled", True))


def _int_setting(cfg: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


def target_day(now: datetime, days_back: int) -> date:
    return (now - timedelta(days=days_back)).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, dtime.min)
    return start, start + timedelta(days=1)


def collect_day_stats(
    day: date,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """重用問候卡的統計查詢，把「昨天」視窗對準指定的那一天。

    ``collect_activity_stats(window="yesterday")`` 的邊界是
    ``[now.date() - 1 天 00:00, now.date() 00:00)``；把 ``now`` 設成隔天午夜，
    邊界就正好是 ``day`` 這一整天。這樣就不必再維護第二套一樣的查詢。
    """
    from core.secretary_greeting import collect_activity_stats

    _, day_end = day_bounds(day)
    return collect_activity_stats(
        window="yesterday", now=day_end, database=database, cfg=cfg, include_usage=True
    )


def per_project_counts(
    day: date,
    *,
    database: Any | None = None,
    limit: int = DEFAULT_MAX_PROJECTS,
) -> list[dict[str, Any]]:
    """當天各專案的 commit／AI 對話／檔案異動筆數（依總量排序）。"""
    database = database or get_db()
    since, until = day_bounds(day)
    buckets: dict[str, dict[str, int]] = {}

    def bump(name: str | None, field: str, count: int) -> None:
        key = (name or "").strip()
        if not key:
            return  # 沒歸戶的活動不猜專案
        buckets.setdefault(key, {"commits": 0, "ai_turns": 0, "files": 0})[field] += count

    with database.session_scope() as session:
        for name, count in (
            session.query(GitActivityEvent.repo_name, func.count(GitActivityEvent.id))
            .filter(GitActivityEvent.timestamp >= since, GitActivityEvent.timestamp < until)
            .group_by(GitActivityEvent.repo_name)
            .all()
        ):
            bump(name, "commits", int(count))
        for name, count in (
            session.query(AIPromptEvent.project_tag, func.count(AIPromptEvent.id))
            .filter(AIPromptEvent.timestamp >= since, AIPromptEvent.timestamp < until)
            .group_by(AIPromptEvent.project_tag)
            .all()
        ):
            bump(name, "ai_turns", int(count))
        for name, count in (
            session.query(FileActivityEvent.project_name, func.count(FileActivityEvent.id))
            .filter(FileActivityEvent.timestamp >= since, FileActivityEvent.timestamp < until)
            .group_by(FileActivityEvent.project_name)
            .all()
        ):
            bump(name, "files", int(count))

    rows = [
        {"project": name, **counts, "total": sum(counts.values())}
        for name, counts in buckets.items()
    ]
    rows.sort(key=lambda item: (-item["total"], item["project"].casefold()))
    return rows[:limit]


def micro_highlights(
    day: date,
    *,
    database: Any | None = None,
    limit: int = DEFAULT_MAX_HIGHLIGHTS,
) -> list[str]:
    """當天已保存的時段微摘要（本模組不重新生成，只挑最長的幾則）。"""
    database = database or get_db()
    since, until = day_bounds(day)
    with database.session_scope() as session:
        rows = (
            session.query(ActivityMicroSummary.period_start, ActivityMicroSummary.summary_text)
            .filter(
                ActivityMicroSummary.period_start >= since,
                ActivityMicroSummary.period_start < until,
            )
            .order_by(ActivityMicroSummary.period_start.asc())
            .all()
        )
    texts = [(start, " ".join(str(text or "").split()).strip()) for start, text in rows]
    texts = [(start, text) for start, text in texts if text]
    # 依內容長度挑代表性的幾則，再依時間排回去，讀起來才是一天的順序。
    chosen = sorted(sorted(texts, key=lambda item: -len(item[1]))[:limit])
    return [text[:MAX_HIGHLIGHT_CHARS] for _, text in chosen]


def _clause(count: int, unit: str, names: list[str] | None = None) -> str:
    text = f"{count} {unit}"
    if names:
        text += f"（{'、'.join(names[:3])}{'…' if len(names) > 3 else ''}）"
    return text


def compose_day_body(
    day: date, stats: dict[str, Any], highlights: list[str]
) -> str:
    """當日觀察的正文；每個數字都對應 stats 裡的一個欄位。"""
    parts: list[str] = []
    if stats.get("commits"):
        parts.append(_clause(stats["commits"], "個 commit", stats.get("commit_repos")))
    if stats.get("prs_touched"):
        parts.append(f"{stats['prs_touched']} 個 PR 有動作")
    if stats.get("ai_turns"):
        parts.append(_clause(stats["ai_turns"], "輪 AI 對話", stats.get("ai_platforms")))
    if stats.get("files_changed"):
        detail = []
        if stats.get("files_writing"):
            detail.append(f"論文文檔 {stats['files_writing']}")
        if stats.get("files_code"):
            detail.append(f"程式 {stats['files_code']}")
        suffix = f"（{'、'.join(detail)}）" if detail else ""
        parts.append(f"改了 {stats['files_changed']} 個檔案{suffix}")
    if stats.get("projects_touched"):
        parts.append(_clause(stats["projects_touched"], "個專案有推進", stats.get("project_names")))
    if stats.get("loops_resolved"):
        parts.append(f"收掉 {stats['loops_resolved']} 個未結事項")
    if stats.get("meetings"):
        parts.append(f"開了 {stats['meetings']} 場會")
    minutes = int(stats.get("foreground_minutes") or 0)
    if minutes >= 30:
        parts.append(f"前景 {minutes // 60} 小時 {minutes % 60} 分")

    if not parts:
        return f"{day.isoformat()}：採集器這天沒有看到任何活動（不代表你沒做事）。"

    body = f"{day.isoformat()} 你做了：" + "、".join(parts) + "。"
    if highlights:
        body += "\n重點：" + "；".join(highlights)
    else:
        body += "\n（這天沒有時段微摘要，以上只有計數；本機摘要 LLM 未啟用或該時段沒產出。）"
    return body[:MAX_BODY_CHARS]


def compose_project_body(day: date, row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row["commits"]:
        parts.append(f"{row['commits']} 個 commit")
    if row["ai_turns"]:
        parts.append(f"{row['ai_turns']} 輪 AI 對話")
    if row["files"]:
        parts.append(f"{row['files']} 個檔案異動")
    return f"{day.isoformat()} · {row['project']}：" + "、".join(parts) + "。"


def build_daily_digest(
    *,
    days_back: int = 1,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    write_memory: bool = True,
) -> dict[str, Any]:
    """把某一天的活動 reduce 成記憶區觀察；唯讀、不呼叫 LLM、可重跑。"""
    cfg = cfg or get_config()
    database = database or get_db()
    now = now or get_local_now()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    days_back = min(MAX_DAYS_BACK, max(0, int(days_back)))
    day = target_day(now, days_back)
    max_projects = _int_setting(
        cfg, "proactive_secretary.daily_digest.max_projects", DEFAULT_MAX_PROJECTS, 1, 20
    )
    max_highlights = _int_setting(
        cfg, "proactive_secretary.daily_digest.max_highlights", DEFAULT_MAX_HIGHLIGHTS, 1, 10
    )

    receipt: dict[str, Any] = {
        "date": day.isoformat(),
        "days_back": days_back,
        "enabled": digest_enabled(cfg),
        "llm_used": False,
        "sources": [
            "git_activity_events", "ai_prompt_events", "file_activity_events",
            "github_pr_events", "open_loops", "activity_micro_summaries",
        ],
        "claim_boundary": DIGEST_CLAIM_BOUNDARY,
    }
    if not digest_enabled(cfg):
        receipt["status"] = "disabled"
        receipt["notes_written"] = 0
        return receipt

    stats = collect_day_stats(day, database=database, cfg=cfg)
    highlights = micro_highlights(day, database=database, limit=max_highlights)
    projects = per_project_counts(day, database=database, limit=max_projects)

    receipt.update({
        "observed_anything": bool(stats.get("observed_anything")),
        "commits": stats.get("commits", 0),
        "ai_turns": stats.get("ai_turns", 0),
        "files_changed": stats.get("files_changed", 0),
        "projects_touched": stats.get("projects_touched", 0),
        "micro_summaries": len(highlights),
        "projects_in_digest": [row["project"] for row in projects],
        "text": compose_day_body(day, stats, highlights),
    })

    written = 0
    if write_memory and stats.get("observed_anything"):
        from core.secretary_memory import memory_enabled, record_observation

        if memory_enabled(cfg):
            def _write(source_ref: str, title: str, body: str, project_key: str | None) -> None:
                nonlocal written
                try:
                    note = record_observation(
                        title=title, body=body, source_ref=source_ref,
                        project_key=project_key, source="daily_digest",
                        database=database, now=now,
                    )
                except Exception as exc:  # noqa: BLE001 — 寫不進去不該讓動作失敗
                    logger.warning("daily digest note not written (%s): %s", source_ref, exc)
                    return
                if note:
                    written += 1

            _write(
                f"daily_digest:{day.isoformat()}",
                f"{day.isoformat()} 工作誌",
                receipt["text"],
                None,
            )
            for row in projects:
                if row["total"] < 2:
                    continue  # 只有一筆事件的專案不值得單獨佔一則記憶
                _write(
                    f"daily_digest:{day.isoformat()}:{row['project']}",
                    f"{day.isoformat()} · {row['project']}",
                    compose_project_body(day, row),
                    row["project"],
                )
        else:
            receipt["memory"] = "disabled"

    receipt["notes_written"] = written
    receipt["status"] = "ok"
    return receipt
