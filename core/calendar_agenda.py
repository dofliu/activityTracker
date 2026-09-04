"""行事曆的唯讀視圖（ADR-015 D3）：今天的行程、下一場、視窗內開了幾場會。

問候卡、晨報與 01 今日面板都只用這裡的函式；每個回傳都帶 ``sources`` 與
``claim_boundary``，數字可回溯到 ``calendar_events``。
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from typing import Any

from core.config import get_config
from core.database import get_db
from core.models import CalendarEvent
from core.time_utils import get_local_now

CALENDAR_CLAIM_BOUNDARY = (
    "行程來自你設定的本機 .ics 檔（唯讀）；只看時間／標題／地點／狀態，不讀描述與與會者。"
    "沒同步進來的行事曆不代表沒有行程。"
)
SHOWN_STATUSES = ("CONFIRMED", "TENTATIVE")
UNTITLED = "行程"


def calendar_enabled(cfg: Any | None = None) -> bool:
    from watchers.calendar_watcher import calendar_effective

    return calendar_effective(cfg or get_config())


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def _serialize(event: CalendarEvent, now: datetime) -> dict[str, Any]:
    start, end = _naive(event.instance_start), _naive(event.instance_end)
    return {
        "uid": event.uid,
        "summary": event.summary or UNTITLED,
        "location": event.location or "",
        "start": start.isoformat(timespec="minutes"),
        "end": end.isoformat(timespec="minutes"),
        "all_day": bool(event.all_day),
        "status": event.status,
        "recurring": bool(event.recurring),
        "calendar_name": event.calendar_name,
        "minutes_until_start": int((start - now).total_seconds() // 60),
        "ongoing": start <= now < end,
        "finished": end <= now,
    }


def events_between(
    since: datetime,
    until: datetime,
    *,
    database: Any | None = None,
    include_cancelled: bool = False,
) -> list[CalendarEvent]:
    database = database or get_db()
    with database.session_scope() as session:
        query = (
            session.query(CalendarEvent)
            .filter(CalendarEvent.instance_end > since, CalendarEvent.instance_start < until)
            .order_by(CalendarEvent.instance_start.asc(), CalendarEvent.all_day.desc())
        )
        if not include_cancelled:
            query = query.filter(CalendarEvent.status.in_(SHOWN_STATUSES))
        rows = query.all()
        session.expunge_all()
    return rows


def day_agenda(
    day: datetime | None = None,
    *,
    now: datetime | None = None,
    database: Any | None = None,
    cfg: Any | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """某一天的行程（預設今天）：全天在前，其餘依開始時間。"""
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    target = _naive(day) if day else now
    day_start = datetime.combine(target.date(), dtime.min)
    day_end = day_start + timedelta(days=1)
    enabled = calendar_enabled(cfg)
    events: list[dict[str, Any]] = []
    if enabled:
        rows = events_between(day_start, day_end, database=database)
        events = [_serialize(row, now) for row in rows[:limit]]
    timed = [e for e in events if not e["all_day"]]
    upcoming = [e for e in timed if not e["finished"]] if target.date() == now.date() else timed
    next_event = next((e for e in upcoming if not e["ongoing"]), None)
    ongoing = next((e for e in timed if e["ongoing"]), None) if target.date() == now.date() else None
    return {
        "date": day_start.date().isoformat(),
        "enabled": enabled,
        "events": events,
        "count": len(events),
        "timed_count": len(timed),
        "all_day_count": len(events) - len(timed),
        "remaining_count": len(upcoming) if target.date() == now.date() else None,
        "ongoing": ongoing,
        "next": next_event,
        "sources": {"events": "calendar_events"},
        "claim_boundary": CALENDAR_CLAIM_BOUNDARY,
    }


def meetings_started_between(
    since: datetime,
    until: datetime | None,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
) -> dict[str, Any]:
    """問候卡用：視窗內**已開始**的非全天行程數（開會負擔），只算未取消的。"""
    cfg = cfg or get_config()
    if not calendar_enabled(cfg):
        return {"enabled": False, "count": 0, "minutes": 0, "titles": []}
    database = database or get_db()
    until = until or (get_local_now())
    with database.session_scope() as session:
        rows = (
            session.query(CalendarEvent)
            .filter(
                CalendarEvent.all_day == False,  # noqa: E712 — SQLite 布林
                CalendarEvent.status.in_(SHOWN_STATUSES),
                CalendarEvent.instance_start >= since,
                CalendarEvent.instance_start < until,
            )
            .order_by(CalendarEvent.instance_start.asc())
            .all()
        )
        minutes = 0
        titles: list[str] = []
        for row in rows:
            start, end = _naive(row.instance_start), _naive(row.instance_end)
            minutes += max(0, int((min(end, until) - start).total_seconds() // 60))
            if row.summary:
                titles.append(row.summary)
    return {"enabled": True, "count": len(rows), "minutes": minutes, "titles": titles[:3]}


def format_event_line(event: dict[str, Any]) -> str:
    """晨報／Telegram 用的一行：``09:30–10:30 專案會議（會議室 A）``；全天寫「全天」。"""
    if event.get("all_day"):
        when = "全天"
    else:
        when = f"{event['start'][11:16]}–{event['end'][11:16]}"
    text = f"{when} {event.get('summary') or UNTITLED}"
    if event.get("location"):
        text += f"（{event['location']}）"
    if event.get("status") == "TENTATIVE":
        text += "・暫定"
    return text


def schedule_sentence(agenda: dict[str, Any]) -> str | None:
    """問候卡的一句話：「今天 3 場行程，下一場 14:00 專案會議（35 分後）」；沒有就 None。"""
    if not agenda.get("enabled") or not agenda.get("count"):
        return None
    parts = [f"今天 {agenda['count']} 場行程"]
    ongoing, nxt = agenda.get("ongoing"), agenda.get("next")
    if ongoing:
        parts.append(f"現在正在「{ongoing['summary']}」")
    if nxt:
        minutes = int(nxt.get("minutes_until_start") or 0)
        when = nxt["start"][11:16]
        if 0 <= minutes < 60:
            parts.append(f"下一場 {when} {nxt['summary']}（{minutes} 分後）")
        else:
            parts.append(f"下一場 {when} {nxt['summary']}")
    elif not ongoing and agenda.get("remaining_count") == 0 and agenda.get("timed_count"):
        parts.append("今天的會都開完了")
    return "，".join(parts) + "。"
