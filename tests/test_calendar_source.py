"""本機行事曆採集來源（ADR-015）的契約。

- 解析：折行、全天、TZID／UTC 換算、RRULE＋EXDATE、RECURRENCE-ID 覆寫、CANCELLED、
  VALARM 跳過；DESCRIPTION／ATTENDEE／ORGANIZER／URL 不落地；只回視野內的實例。
- 掃描：整批替換（取消或移動的不殘留）、壞檔隔離、消失的來源清掉、自我修復。
- 視圖：今天的行程／下一場／剩餘／開會負擔；一句話與一行文字的格式。
- 整合：問候卡（成就句、行程句、claim boundary 改寫、事實閘認得時間）、晨報分節、
  今日面板、端點。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import secretary_greeting as sg
from core.calendar_agenda import (
    CALENDAR_CLAIM_BOUNDARY,
    day_agenda,
    format_event_line,
    meetings_started_between,
    schedule_sentence,
)
from core.ics_parser import default_horizon, parse_duration, parse_ics, parse_property, unfold_lines
from core.models import Base, CalendarEvent
from core.server import app
from watchers.calendar_watcher import (
    CalendarWatcherService,
    calendar_effective,
    calendar_settings,
    discover_ics_files,
)

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 4, 10, 30)  # 週五


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


def _cfg(paths, *, enabled=True, store_titles=True, horizon=30, name="Dof"):
    return DictConfig({
        "watchers": {"calendar_watcher": {
            "enabled": enabled, "paths": [str(p) for p in paths], "horizon_days": horizon, "store_titles": store_titles,
        }},
        "proactive_secretary": {"greeting": {"display_name": name, "llm": {"enabled": False}}, "llm_advisor": {"provider": "ollama"}},
    })


class TempDatabase:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


SAMPLE_ICS = "\r\n".join([
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Test//EN",
    "X-WR-CALNAME:工作",
    # 已結束：09:00–09:30，標題折行，含不該落地的欄位與 VALARM 子區塊
    "BEGIN:VEVENT",
    "UID:standup",
    "DTSTART:20260904T090000",
    "DTEND:20260904T093000",
    "SUMMARY:站立會議",
    " （早）",
    "LOCATION:會議室 A",
    "DESCRIPTION:密碼 1234 不該被存",
    'ATTENDEE;CN="Some:One":mailto:one@example.com',
    "ORGANIZER:mailto:boss@example.com",
    "URL:https://example.com/secret",
    "BEGIN:VALARM",
    "TRIGGER:-PT10M",
    "ACTION:DISPLAY",
    "DESCRIPTION:Reminder",
    "END:VALARM",
    "END:VEVENT",
    # 進行中：10:00–11:00
    "BEGIN:VEVENT",
    "UID:thesis",
    "DTSTART:20260904T100000",
    "DURATION:PT1H",
    "SUMMARY:論文討論",
    "END:VEVENT",
    # 下一場：14:00–15:00
    "BEGIN:VEVENT",
    "UID:project",
    "DTSTART:20260904T140000",
    "DTEND:20260904T150000",
    "SUMMARY:專案會議",
    "LOCATION:會議室 B",
    "END:VEVENT",
    # 全天
    "BEGIN:VEVENT",
    "UID:seminar",
    "DTSTART;VALUE=DATE:20260904",
    "DTEND;VALUE=DATE:20260905",
    "SUMMARY:研討會（全天）",
    "END:VEVENT",
    # 取消的
    "BEGIN:VEVENT",
    "UID:cancelled",
    "DTSTART:20260904T160000",
    "DTEND:20260904T170000",
    "SUMMARY:取消的會",
    "STATUS:CANCELLED",
    "END:VEVENT",
    # 暫定
    "BEGIN:VEVENT",
    "UID:dinner",
    "DTSTART:20260904T173000",
    "DTEND:20260904T180000",
    "SUMMARY:暫定聚餐",
    "STATUS:TENTATIVE",
    "END:VEVENT",
    # 每週五 16:00 週會，9/11 取消，9/18 改到 14:00
    "BEGIN:VEVENT",
    "UID:weekly",
    "DTSTART:20260807T160000",
    "DTEND:20260807T163000",
    "RRULE:FREQ=WEEKLY;BYDAY=FR",
    "EXDATE:20260911T160000",
    "SUMMARY:週會",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:weekly",
    "RECURRENCE-ID:20260918T160000",
    "DTSTART:20260918T140000",
    "DTEND:20260918T143000",
    "SUMMARY:週會（改期）",
    "END:VEVENT",
    # 時區與 UTC（換成本地時間）
    "BEGIN:VEVENT",
    "UID:tz",
    "DTSTART;TZID=Asia/Taipei:20260905T090000",
    "DTEND;TZID=Asia/Taipei:20260905T100000",
    "SUMMARY:台北時間的會",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:utc",
    "DTSTART:20260906T010000Z",
    "DTEND:20260906T020000Z",
    "SUMMARY:UTC 的會",
    "END:VEVENT",
    # 視野外（很久以前）
    "BEGIN:VEVENT",
    "UID:ancient",
    "DTSTART:20250101T090000",
    "DTEND:20250101T100000",
    "SUMMARY:去年的事",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
])


def _parse(text=SAMPLE_ICS, **kw):
    start, end = default_horizon(NOW)
    return parse_ics(text, horizon_start=start, horizon_end=end, **kw)


# ---- 解析 ----


def test_low_level_helpers():
    assert unfold_lines("SUMMARY:a\r\n b\r\nX:1") == ["SUMMARY:ab", "X:1"]
    name, params, value = parse_property('ATTENDEE;CN="Some:One";ROLE=REQ:mailto:x@y')
    assert name == "ATTENDEE" and params == {"CN": "Some:One", "ROLE": "REQ"} and value == "mailto:x@y"
    assert parse_duration("P1DT2H30M") == timedelta(days=1, hours=2, minutes=30)
    assert parse_duration("PT45M") == timedelta(minutes=45) and parse_duration("-P1W") == -timedelta(weeks=1)
    assert parse_duration("nonsense") is None


def test_parse_ics_expands_only_the_horizon_and_drops_private_fields():
    result = _parse()
    assert result.calendar_name == "工作" and result.vevent_count == 11
    by_uid = {}
    for inst in result.instances:
        by_uid.setdefault(inst.uid, []).append(inst)
    assert "ancient" not in by_uid  # 視野外
    standup = by_uid["standup"][0]
    assert standup.summary == "站立會議（早）" and standup.location == "會議室 A"
    assert standup.end == datetime(2026, 9, 4, 9, 30) and standup.status == "CONFIRMED"
    # 內容欄位被丟掉（有計數，證明有丟），任何實例都不帶那些內容
    assert result.dropped_properties >= 4
    assert not any("密碼" in (i.summary + i.location) or "example.com" in (i.summary + i.location) for i in result.instances)
    # DURATION 與全天
    assert by_uid["thesis"][0].end == datetime(2026, 9, 4, 11, 0)
    seminar = by_uid["seminar"][0]
    assert seminar.all_day is True and seminar.start == datetime(2026, 9, 4) and seminar.end == datetime(2026, 9, 5)
    assert by_uid["cancelled"][0].status == "CANCELLED" and by_uid["dinner"][0].status == "TENTATIVE"


def test_recurrence_exdate_and_override():
    result = _parse()
    weekly = sorted((i for i in result.instances if i.uid == "weekly"), key=lambda i: i.start)
    starts = [i.start for i in weekly]
    # 視野 8/28 ～ 10/5：8/28、9/4、(9/11 EXDATE)、9/18 改期 14:00、9/25、10/2
    assert starts == [
        datetime(2026, 8, 28, 16, 0), datetime(2026, 9, 4, 16, 0), datetime(2026, 9, 18, 14, 0),
        datetime(2026, 9, 25, 16, 0), datetime(2026, 10, 2, 16, 0),
    ]
    moved = next(i for i in weekly if i.start == datetime(2026, 9, 18, 14, 0))
    assert moved.summary == "週會（改期）" and moved.recurring is True and moved.end == datetime(2026, 9, 18, 14, 30)
    assert all(i.recurring for i in weekly)


def test_timezones_are_converted_to_local_naive_time():
    result = _parse()
    from zoneinfo import ZoneInfo

    tz = next(i for i in result.instances if i.uid == "tz")
    expected = datetime(2026, 9, 5, 9, 0, tzinfo=ZoneInfo("Asia/Taipei")).astimezone().replace(tzinfo=None)
    assert tz.start == expected and tz.end == expected + timedelta(hours=1)
    utc = next(i for i in result.instances if i.uid == "utc")
    assert utc.start == datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    # 查不到的時區：退回原字面時間並留警告，不讓整份失敗
    odd = _parse("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART;TZID=Mars/Olympus:20260904T090000\nDTEND;TZID=Mars/Olympus:20260904T100000\nSUMMARY:火星\nEND:VEVENT\nEND:VCALENDAR")
    assert odd.instances[0].start == datetime(2026, 9, 4, 9, 0) and "tzid_unresolved:Mars/Olympus" in odd.warnings


def test_store_titles_off_keeps_only_times():
    result = _parse(store_titles=False)
    assert result.instances and all(i.summary == "" and i.location == "" for i in result.instances)


def test_bad_blocks_are_skipped_with_warnings():
    text = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:沒有 UID\nDTSTART:20260904T090000\nEND:VEVENT\nBEGIN:VEVENT\nUID:ok\nDTSTART:20260904T090000\nRRULE:FREQ=BOGUS\nEND:VEVENT\nEND:VCALENDAR"
    result = _parse(text)
    assert "vevent_missing_uid_or_dtstart" in result.warnings
    assert any(w.startswith("rrule_invalid") for w in result.warnings) and result.instances == []


# ---- 掃描 ----


def _write(tmp_path: Path, name="work.ics", text=SAMPLE_ICS) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _service(cfg):
    service = CalendarWatcherService()
    service.cfg = cfg
    return service


def test_effective_only_with_paths(tmp_path):
    assert calendar_effective(_cfg([])) is False
    assert calendar_effective(_cfg([tmp_path], enabled=False)) is False
    assert calendar_effective(_cfg([tmp_path])) is True
    settings = calendar_settings(_cfg([tmp_path], horizon=9999))
    assert settings["horizon_days"] == 366 and settings["scan_interval_seconds"] == 900


def test_discover_files_and_first_level_folders(tmp_path):
    _write(tmp_path, "a.ics")
    (tmp_path / "nested").mkdir()
    _write(tmp_path / "nested", "deep.ics")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    found = discover_ics_files([tmp_path, tmp_path / "a.ics", tmp_path / "missing.ics"])
    assert [p.name for p in found] == ["a.ics"]  # 不遞迴、不重複、忽略非 .ics 與不存在的


def test_scan_writes_replaces_and_forgets_removed_sources(tmp_path):
    db = TempDatabase()
    path = _write(tmp_path)
    service = _service(_cfg([tmp_path]))
    receipt = service.scan_sources(now=NOW, database=db)
    assert receipt["files"] == 1 and receipt["instances"] == 13 and receipt["degraded"] == 0
    with db.session_scope() as s:
        rows = s.query(CalendarEvent).all()
        assert len(rows) == 13 and all(r.source_path == str(path.resolve()) and r.calendar_name == "工作" for r in rows)
        assert not any("密碌" in r.summary or "example.com" in r.summary for r in rows)

    # 使用者刪掉一場會、把另一場移到明天 → 重掃後舊實例不殘留
    changed = SAMPLE_ICS.replace("UID:project\r\nDTSTART:20260904T140000\r\nDTEND:20260904T150000", "UID:project\r\nDTSTART:20260905T140000\r\nDTEND:20260905T150000")
    changed = changed.replace("BEGIN:VEVENT\r\nUID:dinner", "BEGIN:VEVENT\r\nUID:dinner-renamed")
    _write(tmp_path, text=changed)
    service.scan_sources(now=NOW, database=db)
    with db.session_scope() as s:
        starts = {(r.uid, r.instance_start) for r in s.query(CalendarEvent).all()}
        assert ("project", datetime(2026, 9, 5, 14, 0)) in starts and ("project", datetime(2026, 9, 4, 14, 0)) not in starts
        assert ("dinner-renamed", datetime(2026, 9, 4, 17, 30)) in starts and not any(u == "dinner" for u, _ in starts)
        assert len(starts) == 13

    # 檔案從磁碟消失 → 它的實例全部清掉
    path.unlink()
    receipt = service.scan_sources(now=NOW, database=db)
    assert receipt["files"] == 0
    with db.session_scope() as s:
        assert s.query(CalendarEvent).count() == 0
    diag = service.get_diagnostics()
    assert diag["scan_count"] == 3 and diag["sources"] == [] and diag["configured_paths"] == 1


def test_broken_source_is_isolated(tmp_path, monkeypatch):
    db = TempDatabase()
    _write(tmp_path, "good.ics")
    _write(tmp_path, "bad.ics", text="BEGIN:VCALENDAR\nEND:VCALENDAR")
    import watchers.calendar_watcher as cw

    real = cw.parse_ics

    def flaky(text, **kw):
        if "END:VCALENDAR" in text and "BEGIN:VEVENT" not in text:
            raise ValueError("boom")
        return real(text, **kw)

    monkeypatch.setattr(cw, "parse_ics", flaky)
    service = _service(_cfg([tmp_path]))
    receipt = service.scan_sources(now=NOW, database=db)
    assert receipt["files"] == 2 and receipt["instances"] == 13 and receipt["degraded"] == 1
    diag = service.get_diagnostics()
    assert diag["degraded_sources_count"] == 1 and diag["degraded_sources"][0]["source_name"] == "bad.ics"
    assert "ValueError" in diag["degraded_sources"][0]["error"]
    assert [s["source_name"] for s in diag["sources"]] == ["good.ics"]
    assert diag["sources"][0]["dropped_properties"] >= 4 and diag["sources"][0]["instances_in_horizon"] == 13


def test_watcher_lifecycle_and_self_healing(tmp_path):
    # 沒路徑：start 不開執行緒、heal 回 disabled、診斷是 unconfigured
    idle = _service(_cfg([]))
    idle.start()
    assert idle._thread is None and idle.check_health_and_heal() == {"status": "disabled", "healed": False}
    assert idle.get_diagnostics()["state"] == "unconfigured"

    _write(tmp_path)
    service = _service(_cfg([tmp_path]))
    service._running = False
    service._thread = None
    healed = service.check_health_and_heal()
    assert healed["healed"] is True and service._thread is not None and service._thread.is_alive()
    assert service.check_health_and_heal()["status"] == "healthy"
    service.stop()
    assert service.get_diagnostics()["healing_events_count"] == 1


# ---- 視圖 ----


def _seeded_db(tmp_path):
    db = TempDatabase()
    _write(tmp_path)
    _service(_cfg([tmp_path])).scan_sources(now=NOW, database=db)
    return db


def test_day_agenda_next_ongoing_and_remaining(tmp_path):
    db = _seeded_db(tmp_path)
    cfg = _cfg([tmp_path])
    agenda = day_agenda(now=NOW, database=db, cfg=cfg)
    assert agenda["enabled"] is True and agenda["date"] == "2026-09-04"
    # 取消的不算；全天排最前
    assert [e["summary"] for e in agenda["events"]] == ["研討會（全天）", "站立會議（早）", "論文討論", "專案會議", "週會", "暫定聚餐"]
    assert agenda["count"] == 6 and agenda["timed_count"] == 5 and agenda["all_day_count"] == 1
    assert agenda["remaining_count"] == 4  # 論文（進行中）、專案、週會、聚餐
    assert agenda["ongoing"]["summary"] == "論文討論"
    assert agenda["next"]["summary"] == "專案會議" and agenda["next"]["minutes_until_start"] == 210
    assert agenda["sources"] == {"events": "calendar_events"} and agenda["claim_boundary"] == CALENDAR_CLAIM_BOUNDARY
    assert schedule_sentence(agenda) == "今天 6 場行程，現在正在「論文討論」，下一場 14:00 專案會議。"
    assert format_event_line(agenda["events"][3]) == "14:00–15:00 專案會議（會議室 B）"
    assert format_event_line(agenda["events"][0]) == "全天 研討會（全天）"
    assert format_event_line(agenda["events"][5]) == "17:30–18:00 暫定聚餐・暫定"

    # 快到了就講幾分後；另一天沒有 remaining／ongoing 概念
    soon = day_agenda(now=datetime(2026, 9, 4, 13, 40), database=db, cfg=cfg)
    assert schedule_sentence(soon).endswith("下一場 14:00 專案會議（20 分後）。")
    tomorrow = day_agenda(datetime(2026, 9, 5), now=NOW, database=db, cfg=cfg)
    assert tomorrow["remaining_count"] is None and tomorrow["ongoing"] is None and tomorrow["count"] >= 1

    # 沒啟用：誠實回 enabled=False 且沒有一句話
    off = day_agenda(now=NOW, database=db, cfg=_cfg([], enabled=False))
    assert off["enabled"] is False and off["events"] == [] and schedule_sentence(off) is None


def test_meetings_started_between_counts_only_started_and_not_cancelled(tmp_path):
    db = _seeded_db(tmp_path)
    cfg = _cfg([tmp_path])
    since = datetime(2026, 9, 4)
    result = meetings_started_between(since, NOW, database=db, cfg=cfg)
    # 09:00 站立（30 分）＋ 10:00 論文（到 10:30 為止 30 分）；14:00 還沒開始、全天不算
    assert result == {"enabled": True, "count": 2, "minutes": 60, "titles": ["站立會議（早）", "論文討論"]}
    whole_day = meetings_started_between(since, since + timedelta(days=1), database=db, cfg=cfg)
    assert whole_day["count"] == 5  # 取消的不算：站立、論文、專案、週會、聚餐
    assert meetings_started_between(since, NOW, database=db, cfg=_cfg([]))["enabled"] is False


# ---- 整合：問候卡 ----


def test_greeting_counts_meetings_and_rewrites_claim_boundary(tmp_path):
    db = _seeded_db(tmp_path)
    cfg = _cfg([tmp_path])
    greeting = sg.build_greeting(window="today", now=NOW, database=db, cfg=cfg, use_llm=False)
    stats = greeting["stats"]
    assert stats["calendar_enabled"] is True and stats["meetings"] == 2 and stats["meeting_minutes"] == 60
    assert greeting["evidence"]["meetings"] == "calendar_events"
    assert "開了 2 場會（60 分鐘）" in greeting["achievements"]
    assert greeting["schedule_line"] == "今天 6 場行程，現在正在「論文討論」，下一場 14:00 專案會議。"
    assert greeting["schedule"]["count"] == 6 and greeting["schedule"]["next_start"] == "2026-09-04T14:00"
    assert "本機行事曆" in greeting["claim_boundary"] and "郵件目前不在採集範圍" in greeting["claim_boundary"]
    assert "📅 今天 6 場行程" in greeting["text"]
    # 只有會議也算「有活動」，不會被說成沒偵測到
    assert stats["observed_anything"] is True and greeting["encouragement_pool"] != "nothing"
    # 事實閘認得行程句裡的 14:00／6／210 這些數字
    assert sg.llm_text_is_safe("Dof，早安。今天 6 場行程，下一場 14:00 專案會議，加油。", stats) is True
    assert sg.llm_text_is_safe("今天 9 場行程。", stats) is False

    # 近兩小時視窗沒有行程句，但仍會算開會負擔（10:00 論文）
    two_h = sg.build_greeting(window="2h", now=NOW, database=db, cfg=cfg, use_llm=False)
    assert two_h["schedule_line"] is None and two_h["stats"]["meetings"] == 2  # 08:30 之後：09:00 與 10:00

    # 行事曆沒啟用：boundary 維持原句，沒有 meetings 成就
    off = sg.build_greeting(window="today", now=NOW, database=db, cfg=_cfg([], enabled=False), use_llm=False)
    assert off["claim_boundary"] == sg.GREETING_CLAIM_BOUNDARY and off["schedule_line"] is None
    assert not any("場會" in line for line in off["achievements"])


# ---- 整合：晨報、今日面板、端點 ----


def test_morning_briefing_lists_todays_events(monkeypatch, tmp_path):
    from notifiers.messages import build_morning_briefing, render_plain

    db = _seeded_db(tmp_path)
    cfg = _cfg([tmp_path])
    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", lambda **kw: None)
    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", lambda limit=2: {"proposals": []})
    monkeypatch.setattr("core.secretary_greeting.build_greeting", lambda **kw: {
        "headline": "Dof，早安。", "lead": "今天到目前為止，你已經：", "achievements": ["開了 2 場會（60 分鐘）"],
        "schedule_line": "今天 6 場行程，下一場 14:00 專案會議。", "encouragement": "穩。", "source": "rules",
        "stats": {"observed_anything": True},
    })
    import core.calendar_agenda as agenda_module

    real = agenda_module.day_agenda
    monkeypatch.setattr(agenda_module, "day_agenda", lambda **kw: real(now=NOW, database=db, cfg=cfg))
    message = build_morning_briefing(now=NOW, projects=[], open_loops=[], cfg=cfg)
    text = render_plain(message)
    assert "📅 今天 6 場行程，下一場 14:00 專案會議。" in message.sections[0].lines
    calendar = message.sections[1]
    assert calendar.heading == "📅 今日行程（6 場）："
    assert calendar.lines[0] == "• 全天 研討會（全天）" and "• 14:00–15:00 專案會議（會議室 B）" in calendar.lines
    assert "取消的會" not in text
    assert text.index("今日行程") < text.index("今日重點活躍專案")

    # 行事曆讀不到 → 只少那一段
    def boom(**kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr(agenda_module, "day_agenda", boom)
    text = render_plain(build_morning_briefing(now=NOW, projects=[], open_loops=[], cfg=cfg))
    assert "今日行程" not in text and "晨間簡報" in text


def test_today_view_carries_calendar_block(tmp_path):
    from core.secretary_packs import build_today_view

    db = _seeded_db(tmp_path)
    view = build_today_view(database=db, cfg=_cfg([tmp_path]), now=NOW, projects=[])
    calendar = view["calendar"]
    assert calendar["enabled"] is True and calendar["count"] == 6 and calendar["remaining_count"] == 4
    assert calendar["next"]["summary"] == "專案會議" and calendar["line"].startswith("今天 6 場行程")
    assert calendar["claim_boundary"] == CALENDAR_CLAIM_BOUNDARY
    off = build_today_view(database=db, cfg=_cfg([], enabled=False), now=NOW, projects=[])
    assert off["calendar"]["enabled"] is False and off["calendar"]["line"] is None


def test_agenda_endpoint(monkeypatch):
    client = TestClient(app)
    headers = {"Origin": _LOCAL_ORIGIN}
    assert client.get("/api/v1/calendar/agenda?date=2026-13-40", headers=headers).status_code == 400
    captured = {}

    def fake(day=None, **kw):
        captured["day"] = day
        return {"date": "2026-09-04", "enabled": False, "events": [], "count": 0, "claim_boundary": CALENDAR_CLAIM_BOUNDARY}

    monkeypatch.setattr("core.calendar_agenda.day_agenda", fake)
    response = client.get("/api/v1/calendar/agenda?date=2026-09-04", headers=headers)
    assert response.status_code == 200 and response.json()["claim_boundary"] == CALENDAR_CLAIM_BOUNDARY
    assert captured["day"] == datetime(2026, 9, 4)
    assert client.get("/api/v1/calendar/agenda", headers=headers).status_code == 200 and captured["day"] is None
