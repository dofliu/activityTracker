"""通道中立的推播訊息模型與組裝（ADR-014）。

推播原本只有 Telegram，訊息在組裝時就寫死 HTML 標籤。要讓同一份內容也能送到
LINE（純文字、不支援 HTML／Markdown）或未來其他通道，必須先把**內容**與
**呈現**分開：

- :class:`Message` 只描述結構（標題、分節、footer），不含任何標記語法；
- :func:`render_plain` 給 LINE 與 CLI 預覽，:func:`render_telegram_html` 給
  Telegram（``<b>`` 粗體）。新增通道只要再寫一個 renderer。

契約：組裝函式只讀既有的唯讀來源（專案狀態、Open Loops、每日摘要、早晨包
收據、秘書建議），任何子步驟失敗都省略該段而不是讓整則推播消失。
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.Messages")


@dataclass(frozen=True)
class Section:
    """一個分節：可有標題，內容是已格式化的短行。"""

    heading: str | None = None
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Message:
    title: str
    sections: tuple[Section, ...] = ()
    footer: str | None = None


def render_plain(message: Message) -> str:
    """純文字（LINE、CLI 預覽）：不含任何標記語法。"""
    parts: list[str] = [message.title]
    for section in message.sections:
        parts.append("")
        if section.heading:
            parts.append(section.heading)
        parts.extend(section.lines)
    if message.footer:
        parts.extend(["", message.footer])
    return "\n".join(parts)


def render_telegram_html(message: Message) -> str:
    """Telegram HTML：標題與分節標題加粗；內容一律 escape，避免解析失敗。"""
    parts: list[str] = [f"<b>{html.escape(message.title)}</b>"]
    for section in message.sections:
        parts.append("")
        if section.heading:
            parts.append(f"<b>{html.escape(section.heading)}</b>")
        parts.extend(html.escape(line) for line in section.lines)
    if message.footer:
        parts.extend(["", f"<i>{html.escape(message.footer)}</i>"])
    return "\n".join(parts)


def _projects_and_loops(
    projects: Sequence[dict[str, Any]] | None,
    open_loops: Sequence[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if projects is None or open_loops is None:
        from core.project_engine import get_active_projects_list, get_open_loops_list

        projects = projects if projects is not None else get_active_projects_list()
        open_loops = open_loops if open_loops is not None else get_open_loops_list()
    return list(projects), list(open_loops)


def _loop_lines(open_loops: Sequence[dict[str, Any]], limit: int = 6) -> tuple[str, ...]:
    if not open_loops:
        return ("• （目前無待辦未結事項）",)
    return tuple(
        f"• [ ] [{item.get('project_key')}] {item.get('title')}" for item in open_loops[:limit]
    )


def _pack_line(now: datetime) -> str | None:
    """早晨包收據的一行摘要；沒有排程或讀不到就不說話。"""
    try:
        from core.secretary_packs import latest_pack_summary, pack_summary_line

        return pack_summary_line(latest_pack_summary(now=now))
    except Exception as exc:  # noqa: BLE001 — 收據讀不到不該讓推播消失
        logger.debug("pack line unavailable: %s", type(exc).__name__)
        return None


def _secretary_section(limit: int = 2) -> Section | None:
    """秘書 top 建議（唯讀）；秘書層失敗不阻斷推播本體。"""
    try:
        from core.proactive_secretary import briefing_proposals

        result = briefing_proposals(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("briefing proposals unavailable: %s", type(exc).__name__)
        return None
    proposals = result.get("proposals") or []
    if not proposals:
        return None
    lines: list[str] = []
    for item in proposals:
        lines.append(f"• [{item.get('project_key')}] {item.get('title')}")
        if item.get("why_now"):
            lines.append(f"  為什麼是現在：{item['why_now']}")
    total = result.get("total") or len(proposals)
    heading = f"🤖 待判斷建議（共 {total} 項）：" if total else "🤖 待判斷建議："
    if result.get("advisor_summary"):
        lines.append(f"  {result['advisor_summary']}")
    return Section(heading=heading, lines=tuple(lines))


# ---------------------------------------------------------------- 組裝


def build_morning_briefing(
    *,
    now: datetime | None = None,
    projects: Sequence[dict[str, Any]] | None = None,
    open_loops: Sequence[dict[str, Any]] | None = None,
    include_secretary: bool = True,
) -> Message:
    now = now or get_local_now()
    projects, open_loops = _projects_and_loops(projects, open_loops)
    active = [p for p in projects if p.get("status") == "active"][:5]

    sections: list[Section] = []
    if active:
        sections.append(Section(
            heading="🔥 今日重點活躍專案：",
            lines=tuple(f"• {p.get('display_name')}：{p.get('last_action_summary')}" for p in active),
        ))
    else:
        sections.append(Section(heading="🔥 今日重點活躍專案：", lines=("• （目前尚無高頻專案）",)))

    pack = _pack_line(now)
    if pack:
        sections.append(Section(lines=(pack,)))

    sections.append(Section(
        heading=f"📌 待跟進未結事項（{len(open_loops)} 項）：",
        lines=_loop_lines(open_loops),
    ))

    secretary = _secretary_section() if include_secretary else None
    if secretary:
        sections.append(secretary)

    return Message(
        title=f"🌅 OmniContext 晨間簡報（{now.strftime('%Y-%m-%d')}）",
        sections=tuple(sections),
        # 沒有建議就不要留一句談建議的邊界說明
        footer="建議僅供判斷，不會自動執行。" if secretary else None,
    )


def build_evening_handoff(
    *,
    now: datetime | None = None,
    projects: Sequence[dict[str, Any]] | None = None,
    open_loops: Sequence[dict[str, Any]] | None = None,
) -> Message:
    """晚間交接（唯讀盤點）：只推觀測到的事實，不歸檔、不改任何資料。"""
    now = now or get_local_now()
    projects, open_loops = _projects_and_loops(projects, open_loops)
    today = now.strftime("%Y-%m-%d")
    touched = [p for p in projects if str(p.get("last_activity_at", "")).startswith(today)]

    sections: list[Section] = []
    if touched:
        sections.append(Section(
            heading=f"今日推進 {len(touched)} 個專案：",
            lines=tuple(f"• {p.get('display_name')}：{p.get('last_action_summary')}" for p in touched[:6]),
        ))
    else:
        sections.append(Section(lines=("今天沒有偵測到專案活動。",)))
    sections.append(Section(
        heading=f"📌 未結事項盤點（{len(open_loops)} 項待跟進）：",
        lines=_loop_lines(open_loops),
    ))
    return Message(
        title=f"🌙 OmniContext 晚間交接（{now.strftime('%Y-%m-%d')}）",
        sections=tuple(sections),
        footer="此為唯讀盤點；明早晨報會再附上待判斷建議。",
    )


def build_daily_summary(date_str: str, *, raw_markdown: str | None = None, max_chars: int = 3600) -> Message | None:
    """每日全景工作日報；找不到當日摘要就回 None（不推空訊息）。"""
    if raw_markdown is None:
        from core.database import get_db
        from core.models import DailySummary

        db = get_db()
        with db.session_scope() as session:
            row = session.query(DailySummary).filter_by(date_str=date_str).first()
            if not row:
                logger.warning("No summary found for %s to push.", date_str)
                return None
            raw_markdown = row.raw_markdown or ""
    body = str(raw_markdown)
    truncated = len(body) > max_chars
    lines = tuple((body[:max_chars] + ("\n…（已截斷，完整內容見儀表板）" if truncated else "")).splitlines())
    return Message(
        title=f"📅 OmniContext 每日全景工作日報（{date_str}）",
        sections=(Section(lines=lines),),
    )


def build_stagnation_alert(
    *,
    projects: Sequence[dict[str, Any]] | None = None,
    min_idle_days: int = 3,
    limit: int = 4,
) -> Message | None:
    """停滯專案提醒；沒有符合門檻的專案就回 None。"""
    if projects is None:
        from core.project_engine import get_active_projects_list

        projects = get_active_projects_list()
    stagnant = [
        p for p in projects
        if p.get("status") in ("idle", "stale") and int(p.get("idle_days") or 0) >= min_idle_days
    ][:limit]
    if not stagnant:
        return None
    return Message(
        title="⚠️ OmniContext 專案停滯提醒",
        sections=(Section(
            heading=f"以下 {len(stagnant)} 個專案已閒置 {min_idle_days} 天以上：",
            lines=tuple(
                f"• {p.get('display_name')}：閒置 {p.get('idle_days')} 天"
                f"（最後：{p.get('last_action_summary')}）"
                for p in stagnant
            ),
        ),),
        footer="閒置不等於停擺；這只是提醒您確認是否還要推進。",
    )
