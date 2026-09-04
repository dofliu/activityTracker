"""最小可用的 RFC 5545（iCalendar）VEVENT 解析（ADR-015）。

只做決策需要的事：把一份 `.ics` 展開成「視野內的行程實例」清單。

- 只用標準函式庫 ＋ ``python-dateutil``（RRULE 展開）；不引入 icalendar 套件。
- **只取** UID／SUMMARY／LOCATION／DTSTART／DTEND／DURATION／RRULE／EXDATE／
  RECURRENCE-ID／STATUS／LAST-MODIFIED；DESCRIPTION、ATTENDEE、ORGANIZER、URL、
  VALARM 一律不讀進來（ADR-015 D2）。
- 時間一律換成**本地 naive datetime**，與資料庫其他事件一致。TZID 查不到就退回
  本地時間並在回傳的 ``warnings`` 記一筆，不讓整份檔案失敗。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any, Iterable

logger = logging.getLogger("OmniContext.ICS")

DROPPED_PROPERTIES = frozenset({"DESCRIPTION", "ATTENDEE", "ORGANIZER", "URL", "ATTACH", "COMMENT", "CONTACT"})
_UTC_SUFFIX = "Z"


@dataclass(frozen=True)
class EventInstance:
    uid: str
    start: datetime
    end: datetime
    all_day: bool
    summary: str
    location: str
    status: str  # CONFIRMED / TENTATIVE / CANCELLED
    recurring: bool
    last_modified: datetime | None = None


@dataclass
class ParseResult:
    calendar_name: str | None
    instances: list[EventInstance] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    vevent_count: int = 0
    dropped_properties: int = 0  # 依 D2 丟掉的內容欄位數（只計數，證明有丟）


# ---------------------------------------------------------------- 低階：折行與屬性


def unfold_lines(text: str) -> list[str]:
    """RFC 5545 §3.1：以空白或 TAB 開頭的行是上一行的延續。"""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw:
            continue
        if raw[0] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_property(line: str) -> tuple[str, dict[str, str], str]:
    """``NAME;PARAM=VALUE;PARAM2=V2:value`` → (NAME, {PARAM: VALUE}, value)。"""
    # 冒號可能出現在被引號包住的參數值裡（例如 CN="a:b"），先找第一個不在引號內的冒號
    in_quotes = False
    split_at = -1
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ":" and not in_quotes:
            split_at = index
            break
    if split_at < 0:
        return line.strip().upper(), {}, ""
    head, value = line[:split_at], line[split_at + 1:]
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value


def unescape_text(value: str) -> str:
    return (
        value.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    ).strip()


# ---------------------------------------------------------------- 時間


def _zone(tzid: str | None):
    if not tzid:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 — 找不到時區資料（Windows 無 tzdata）或未知 TZID
        return None


def parse_datetime(value: str, params: dict[str, str], warnings: list[str]) -> tuple[datetime | None, bool]:
    """回傳 (本地 naive datetime, all_day)。解析失敗回 (None, False)。"""
    value = value.strip()
    if not value:
        return None, False
    if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and value.isdigit()):
        try:
            day = datetime.strptime(value, "%Y%m%d")
        except ValueError:
            return None, False
        return day, True
    utc = value.endswith(_UTC_SUFFIX)
    core = value[:-1] if utc else value
    try:
        parsed = datetime.strptime(core, "%Y%m%dT%H%M%S")
    except ValueError:
        try:
            parsed = datetime.strptime(core, "%Y%m%dT%H%M")
        except ValueError:
            return None, False
    if utc:
        from datetime import timezone

        return parsed.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None), False
    tzid = params.get("TZID")
    if tzid:
        zone = _zone(tzid)
        if zone is None:
            note = f"tzid_unresolved:{tzid}"
            if note not in warnings:
                warnings.append(note)
            return parsed, False
        return parsed.replace(tzinfo=zone).astimezone().replace(tzinfo=None), False
    return parsed, False


def parse_duration(value: str) -> timedelta | None:
    """RFC 5545 DURATION（P1DT2H30M / PT45M / -P1D）。"""
    value = value.strip().upper()
    if not value:
        return None
    sign = -1 if value.startswith("-") else 1
    value = value.lstrip("+-")
    if not value.startswith("P"):
        return None
    days = hours = minutes = seconds = weeks = 0
    number = ""
    in_time = False
    for char in value[1:]:
        if char.isdigit():
            number += char
            continue
        if char == "T":
            in_time = True
            continue
        amount = int(number or 0)
        number = ""
        if char == "W":
            weeks = amount
        elif char == "D":
            days = amount
        elif char == "H" and in_time:
            hours = amount
        elif char == "M" and in_time:
            minutes = amount
        elif char == "S" and in_time:
            seconds = amount
        else:
            return None
    return sign * timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


# ---------------------------------------------------------------- VEVENT 區塊


def _split_vevents(lines: Iterable[str]) -> tuple[str | None, list[list[str]]]:
    calendar_name: str | None = None
    blocks: list[list[str]] = []
    current: list[str] | None = None
    depth = 0  # VEVENT 內可能有 VALARM 子區塊，要整塊跳過
    for line in lines:
        upper = line.upper()
        if upper.startswith("BEGIN:VEVENT"):
            current = []
            depth = 0
            continue
        if current is not None:
            if upper.startswith("BEGIN:"):
                depth += 1
                continue
            if upper.startswith("END:VEVENT") and depth == 0:
                blocks.append(current)
                current = None
                continue
            if upper.startswith("END:"):
                depth = max(0, depth - 1)
                continue
            if depth == 0:
                current.append(line)
            continue
        if upper.startswith("X-WR-CALNAME:"):
            calendar_name = unescape_text(line.split(":", 1)[1])[:120] or None
    return calendar_name, blocks


def _expand_rrule(
    rrule_value: str,
    dtstart: datetime,
    *,
    horizon_start: datetime,
    horizon_end: datetime,
    exdates: set[datetime],
    warnings: list[str],
    max_instances: int = 500,
) -> list[datetime]:
    try:
        from dateutil.rrule import rrulestr
    except ImportError:  # pragma: no cover — dateutil 是宣告相依
        warnings.append("dateutil_missing")
        return []
    try:
        rule = rrulestr(rrule_value, dtstart=dtstart, forceset=True)
    except Exception as exc:  # noqa: BLE001 — 壞規則只影響這一筆
        warnings.append(f"rrule_invalid:{type(exc).__name__}")
        return []
    # 展開下界要含 dtstart 之前不可能有實例；上界用 horizon_end
    lower = max(horizon_start, dtstart) - timedelta(days=1)
    starts: list[datetime] = []
    try:
        for occurrence in rule.between(lower, horizon_end, inc=True):
            occurrence = occurrence.replace(tzinfo=None) if occurrence.tzinfo else occurrence
            if occurrence in exdates:
                continue
            starts.append(occurrence)
            if len(starts) >= max_instances:
                warnings.append("rrule_truncated")
                break
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"rrule_expand_failed:{type(exc).__name__}")
    return starts


def parse_ics(
    text: str,
    *,
    horizon_start: datetime,
    horizon_end: datetime,
    store_titles: bool = True,
) -> ParseResult:
    """把一份 `.ics` 展開成 ``[horizon_start, horizon_end)`` 內的行程實例。

    只有「與視野有交集」的實例會回傳；RECURRENCE-ID 覆寫會取代同 UID 同起點的
    週期實例；STATUS:CANCELLED 的實例仍回傳（status=CANCELLED），由呼叫端決定
    要不要顯示——這樣「被取消的會」也能如實留下痕跡而不被當成沒發生。
    """
    calendar_name, blocks = _split_vevents(unfold_lines(text))
    result = ParseResult(calendar_name=calendar_name, vevent_count=len(blocks))
    warnings = result.warnings

    parsed_blocks: list[dict[str, Any]] = []
    for block in blocks:
        item: dict[str, Any] = {"exdates": set()}
        for line in block:
            name, params, value = parse_property(line)
            if name in DROPPED_PROPERTIES:
                result.dropped_properties += 1
                continue
            if name == "UID":
                item["uid"] = value.strip()[:255]
            elif name == "SUMMARY":
                item["summary"] = unescape_text(value)[:200]
            elif name == "LOCATION":
                item["location"] = unescape_text(value)[:200]
            elif name == "STATUS":
                item["status"] = value.strip().upper()[:24]
            elif name == "DTSTART":
                item["dtstart"], item["all_day"] = parse_datetime(value, params, warnings)
            elif name == "DTEND":
                item["dtend"], _ = parse_datetime(value, params, warnings)
            elif name == "DURATION":
                item["duration"] = parse_duration(value)
            elif name == "RRULE":
                item["rrule"] = value.strip()
            elif name == "EXDATE":
                for piece in value.split(","):
                    when, _ = parse_datetime(piece, params, warnings)
                    if when:
                        item["exdates"].add(when)
            elif name == "RECURRENCE-ID":
                item["recurrence_id"], _ = parse_datetime(value, params, warnings)
            elif name == "LAST-MODIFIED":
                item["last_modified"], _ = parse_datetime(value, params, warnings)
        if not item.get("uid") or not item.get("dtstart"):
            warnings.append("vevent_missing_uid_or_dtstart")
            continue
        parsed_blocks.append(item)

    def _end_for(item: dict[str, Any], start: datetime) -> datetime:
        if item.get("dtend") and item.get("dtstart"):
            return start + (item["dtend"] - item["dtstart"])
        if item.get("duration"):
            return start + item["duration"]
        return start + (timedelta(days=1) if item.get("all_day") else timedelta(0))

    def _make(item: dict[str, Any], start: datetime, recurring: bool) -> EventInstance:
        return EventInstance(
            uid=item["uid"],
            start=start,
            end=_end_for(item, start),
            all_day=bool(item.get("all_day")),
            summary=(item.get("summary") or "") if store_titles else "",
            location=(item.get("location") or "") if store_titles else "",
            status=item.get("status") or "CONFIRMED",
            recurring=recurring,
            last_modified=item.get("last_modified"),
        )

    # 先收覆寫（RECURRENCE-ID），之後展開週期時把同起點的實例換掉
    overrides: dict[tuple[str, datetime], dict[str, Any]] = {}
    for item in parsed_blocks:
        if item.get("recurrence_id"):
            overrides[(item["uid"], item["recurrence_id"])] = item

    instances: dict[tuple[str, datetime], EventInstance] = {}
    for item in parsed_blocks:
        if item.get("recurrence_id"):
            continue  # 覆寫在下面併入
        if item.get("rrule"):
            starts = _expand_rrule(
                item["rrule"], item["dtstart"],
                horizon_start=horizon_start, horizon_end=horizon_end,
                exdates=item["exdates"], warnings=warnings,
            )
            for start in starts:
                key = (item["uid"], start)
                override = overrides.pop(key, None)
                if override:
                    instance = _make(override, override["dtstart"], True)
                    instances[(item["uid"], instance.start)] = instance
                else:
                    instances[key] = _make(item, start, True)
        else:
            instances[(item["uid"], item["dtstart"])] = _make(item, item["dtstart"], False)

    # 沒對到母事件的覆寫（母事件可能不在視野內）：自己就是一個實例
    for (uid, _rid), override in overrides.items():
        instance = _make(override, override["dtstart"], True)
        instances.setdefault((uid, instance.start), instance)

    result.instances = sorted(
        (
            inst for inst in instances.values()
            if inst.end > horizon_start and inst.start < horizon_end
        ),
        key=lambda inst: (inst.start, inst.uid),
    )
    return result


def default_horizon(now: datetime, *, horizon_days: int = 30, lookback_days: int = 7) -> tuple[datetime, datetime]:
    day_start = datetime.combine(now.date(), dtime.min)
    return day_start - timedelta(days=max(0, lookback_days)), day_start + timedelta(days=max(1, horizon_days) + 1)


__all__ = [
    "DROPPED_PROPERTIES",
    "EventInstance",
    "ParseResult",
    "default_horizon",
    "parse_datetime",
    "parse_duration",
    "parse_ics",
    "parse_property",
    "unfold_lines",
]

