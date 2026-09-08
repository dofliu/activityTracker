"""會議秘書第一層的契約（ADR-022）。

使用者在線上會議問秘書「你可以看到嗎」，秘書答不能——這是對的。第一層要做的是
**會後**：你把 Teams 匯出的逐字稿放進一個資料夾，秘書整理成可回溯的紀錄。

這裡鎖住的是邊界，不是「摘要寫得好不好」：
1. WebVTT 解析：編號與時間戳去掉、兩種講者標記都認、字幕「長出來」的重複要收斂。
2. 配對只用時間；配不到就如實說「未配對」，不猜主題。
3. 摘要失敗（或被事實閘擋下）不會讓逐字稿紀錄消失。
4. **候選待辦要你點了才成為未結事項**——秘書自己永遠不寫 open_loops。
5. 「在開會」需要兩個確定性訊號都成立；只有其一時說出差異。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import meeting_transcripts as mt
from core.models import Base, CalendarEvent, OpenLoop, SecretaryNote, WindowEvent

NOW = datetime(2026, 9, 8, 16, 0)

VTT = """WEBVTT

NOTE recorded by Microsoft Teams

1
00:00:01.000 --> 00:00:04.000
<v Dof Liu>那我們下週三對一次進度

2
00:00:04.500 --> 00:00:08.000
<v Dof Liu>那我們下週三對一次進度，我會先把報告寄出去

3
00:00:09.000 --> 00:00:12.000
Amy: 好，我負責整理客戶回饋，大概 3 份

4
00:00:13.000 --> 00:00:15.000
<v Dof Liu>那就這樣
"""


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


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    """一個逐字稿資料夾 ＋ 指向它的設定。"""
    directory = tmp_path / "meetings"
    directory.mkdir()
    monkeypatch.setattr(mt, "resolve_runtime_path", lambda value: Path(value))
    return directory


def _cfg(directory: Path | None, *, provider="ollama", memory=True):
    # 行事曆要「啟用且有路徑」才算在採集（calendar_effective）；配對與「在開會」
    # 都依賴它，所以測試設定必須把那條開起來。
    return DictConfig({
        "meetings": {"transcript_dir": str(directory) if directory else "", "provider": provider},
        "secretary_memory": {"enabled": memory},
        "watchers": {"calendar_watcher": {"enabled": True, "paths": ["C:/cal"]}},
    })


def _write(directory: Path, name: str, content: str, *, minutes_ago: int = 5) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    stamp = (NOW - timedelta(minutes=minutes_ago)).timestamp()
    import os

    os.utime(path, (stamp, stamp))
    return path


def _event(db, summary, *, start_minutes_ago, minutes=60, all_day=False):
    with db.session_scope() as session:
        session.add(CalendarEvent(
            uid=f"uid-{summary}",
            instance_start=NOW - timedelta(minutes=start_minutes_ago),
            instance_end=NOW - timedelta(minutes=start_minutes_ago - minutes),
            all_day=all_day,
            summary=summary,
            status="CONFIRMED",
            source_path=f"C:/cal/{summary}.ics",
        ))


# ---- 1. WebVTT 解析 ----


def test_webvtt_strips_cues_and_collapses_growing_captions():
    turns = mt.parse_webvtt(VTT)
    assert [item["speaker"] for item in turns] == ["Dof Liu", "Amy", "Dof Liu"]
    # 同一句在字幕裡長出來 → 只留最長的那一版，不是兩段
    assert turns[0]["text"] == "那我們下週三對一次進度，我會先把報告寄出去"
    assert turns[1]["text"].startswith("好，我負責整理客戶回饋")
    body = "\n".join(str(item["text"]) for item in turns)
    assert "00:00" not in body and "WEBVTT" not in body and "recorded by" not in body


def test_plain_text_transcript_needs_no_vtt_syntax(transcripts):
    path = _write(transcripts, "notes.txt", "Dof: 先做 A\nAmy: 我接 B\n")
    turns = mt.transcript_turns(path)
    assert [(item["speaker"], item["text"]) for item in turns] == [("Dof", "先做 A"), ("Amy", "我接 B")]


def test_disabled_when_no_directory_is_configured(transcripts):
    files, meta = mt.list_transcripts(cfg=_cfg(None), now=NOW)
    assert files == [] and meta == {"used": False, "reason": "no_transcript_dir"}
    assert mt.meetings_enabled(_cfg(None)) is False


def test_listing_is_newest_first_and_ignores_other_files(transcripts):
    _write(transcripts, "old.vtt", VTT, minutes_ago=600)
    _write(transcripts, "new.vtt", VTT, minutes_ago=5)
    _write(transcripts, "ignore.mp4", "binary-ish", minutes_ago=1)
    files, meta = mt.list_transcripts(cfg=_cfg(transcripts), now=NOW)
    assert [item["name"] for item in files] == ["new.vtt", "old.vtt"]
    assert meta["used"] is True and meta["files"] == 2


# ---- 2. 配對只用時間 ----


def test_pairing_picks_the_meeting_that_just_ended(transcripts):
    db = TempDatabase()
    _event(db, "早上的專案會議", start_minutes_ago=300, minutes=60)   # 結束於 4 小時前
    _event(db, "剛剛的會議", start_minutes_ago=90, minutes=60)        # 結束於 30 分前
    paired = mt.pair_with_calendar(NOW - timedelta(minutes=5), database=db, cfg=_cfg(transcripts))
    assert paired is not None and paired["summary"] == "剛剛的會議"
    assert paired["minutes_after_end"] == 25


def test_pairing_returns_none_outside_the_window_and_for_all_day(transcripts):
    db = TempDatabase()
    _event(db, "太久以前", start_minutes_ago=60 * 20, minutes=60)
    _event(db, "全天", start_minutes_ago=120, minutes=60, all_day=True)
    assert mt.pair_with_calendar(NOW, database=db, cfg=_cfg(transcripts)) is None


# ---- 3. 摘要與事實閘 ----


def _notes(db):
    from core.secretary_memory import list_notes

    return list_notes(kind="observation", limit=50, database=db)["notes"]


def test_notes_record_summary_and_followups_once_per_file(transcripts):
    db = TempDatabase()
    _event(db, "進度會議", start_minutes_ago=90, minutes=60)
    _write(transcripts, "meeting.vtt", VTT)
    reply = "摘要：\n- 討論下週進度\n待辦候選：\n- Dof 寄出報告\n- Amy 整理客戶回饋"

    result = mt.build_meeting_notes(
        database=db, cfg=_cfg(transcripts), now=NOW, llm_generate=lambda s, u: reply
    )
    assert result["written_count"] == 1 and result["provider"] == "ollama"
    assert result["provider_is_cloud"] is False
    note = _notes(db)[0]
    assert note["title"] == "會議紀錄：進度會議"
    assert "討論下週進度" in note["body"] and "會議：進度會議" in note["body"]
    assert [item["text"] for item in mt.parse_followups(note["body"])] == ["Dof 寄出報告", "Amy 整理客戶回饋"]

    # 同一個檔案重跑不會產生第二則
    again = mt.build_meeting_notes(
        database=db, cfg=_cfg(transcripts), now=NOW, llm_generate=lambda s, u: reply
    )
    assert again["written_count"] == 0 and again["skipped"][0]["reason"] == "already_recorded"
    assert len(_notes(db)) == 1


def test_unpaired_transcript_says_so_instead_of_guessing(transcripts):
    db = TempDatabase()
    _write(transcripts, "somewhere.vtt", VTT)
    mt.build_meeting_notes(database=db, cfg=_cfg(transcripts), now=NOW, llm_generate=lambda s, u: "摘要：\n- 聊了一下")
    body = _notes(db)[0]["body"]
    assert "未配對到行事曆事件" in body and "somewhere.vtt" in body


def test_fact_gate_drops_a_summary_that_invents_numbers(transcripts):
    db = TempDatabase()
    _write(transcripts, "m.vtt", VTT)
    invented = "摘要：\n- 我們決定投入 250 萬預算\n待辦候選：\n- 簽約"
    mt.build_meeting_notes(database=db, cfg=_cfg(transcripts), now=NOW, llm_generate=lambda s, u: invented)
    body = _notes(db)[0]["body"]
    assert "事實閘擋下" in body and "250" in body.split("事實閘擋下")[1][:60]
    assert "投入 250 萬預算" not in body
    assert mt.parse_followups(body) == []          # 摘要不可信時候選待辦一起丟掉
    # 逐字稿本身的紀錄仍在
    assert "4 位講者" not in body and "段發言" in body


def test_llm_failure_still_keeps_the_transcript_record(transcripts):
    db = TempDatabase()
    _write(transcripts, "m.vtt", VTT)

    def _boom(system, user):
        raise TimeoutError("ollama 沒有回應")

    result = mt.build_meeting_notes(database=db, cfg=_cfg(transcripts), now=NOW, llm_generate=_boom)
    assert result["written_count"] == 1 and result["errors"] == []
    body = _notes(db)[0]["body"]
    assert "摘要：失敗（TimeoutError" in body and "段發言" in body


def test_provider_error_string_is_not_reported_as_an_invented_number(transcripts):
    """LLMClient 連不上供應商時是**回傳錯誤字串**而不是丟例外。容器實測（2026-09-08）：
    ollama 沒開，那串錯誤被當成摘要送進事實閘，觀察上寫「摘要編造了數字 11434」——
    真正的原因是 ollama 沒開。訊息指錯原因也是 bug。"""
    db = TempDatabase()
    _write(transcripts, "m.vtt", VTT)
    error_reply = (
        "[Ollama 錯誤] HTTPConnectionPool(host='localhost', port=11434): "
        "Max retries exceeded with url: /api/generate"
    )
    result = mt.build_meeting_notes(
        database=db, cfg=_cfg(transcripts), now=NOW, llm_generate=lambda s, u: error_reply
    )
    assert result["written_count"] == 1
    body = _notes(db)[0]["body"]
    assert "provider 未回覆摘要" in body and "11434" in body
    assert "事實閘擋下" not in body           # 不是編造數字，是連不上
    assert "段發言" in body                   # 逐字稿紀錄仍在

    assert mt.looks_like_llm_error("摘要：\n- 正常的一句話") is None
    assert mt.looks_like_llm_error("") == "供應商沒有回應任何內容"


def test_cloud_provider_is_named_in_the_record(transcripts):
    db = TempDatabase()
    _write(transcripts, "m.vtt", VTT)
    mt.build_meeting_notes(
        database=db, cfg=_cfg(transcripts, provider="gemini"), now=NOW,
        llm_generate=lambda s, u: "摘要：\n- 一句話",
    )
    body = _notes(db)[0]["body"]
    assert "摘要 provider：gemini（雲端：與會者的發言曾送往該供應商）" in body


def test_fact_gate_allows_the_numbers_it_was_given():
    gate = mt.fact_gate("三段發言、共 3 份回饋", "客戶回饋，大概 3 份", allowed=[3])
    assert gate["ok"] is True
    assert mt.fact_gate("預算 250 萬", "沒有數字")["unsupported_numbers"] == ["250"]


# ---- 4. 候選待辦要你點了才算 ----


def test_followup_markers_round_trip():
    body = "摘要：\n- x\n待辦候選（要你點了才會成為未結事項）：\n- 寄報告\n- 整理回饋"
    marked = mt.mark_followup(body, 0, "accepted")
    items = mt.parse_followups(marked)
    assert [(item["text"], item["status"]) for item in items] == [("寄報告", "accepted"), ("整理回饋", "pending")]
    twice = mt.mark_followup(marked, 1, "ignored")
    assert [item["status"] for item in mt.parse_followups(twice)] == ["accepted", "ignored"]
    with pytest.raises(ValueError):
        mt.mark_followup(body, 5, "accepted")


def test_accepting_a_followup_is_the_only_path_that_writes_open_loops(transcripts, monkeypatch):
    db = TempDatabase()
    import core.project_engine as pe

    monkeypatch.setattr(pe, "get_db", lambda: db)
    _write(transcripts, "m.vtt", VTT)
    mt.build_meeting_notes(
        database=db, cfg=_cfg(transcripts), now=NOW,
        llm_generate=lambda s, u: "摘要：\n- x\n待辦候選：\n- 寄出報告\n- 整理回饋",
    )
    note_id = _notes(db)[0]["id"]

    # 產生觀察本身不會寫任何未結事項
    with db.session_scope() as session:
        assert session.query(OpenLoop).count() == 0

    accepted = mt.accept_followup(note_id, 0, action="accept", project_key="alpha", database=db)
    assert accepted["status"] == "accepted" and accepted["open_loop_id"]
    with db.session_scope() as session:
        loops = session.query(OpenLoop).all()
        assert len(loops) == 1 and loops[0].source_type == "meeting"
        assert "寄出報告" in loops[0].title

    ignored = mt.accept_followup(note_id, 1, action="ignore", database=db)
    assert ignored["status"] == "ignored"
    with db.session_scope() as session:
        assert session.query(OpenLoop).count() == 1        # 忽略不寫任何東西

    repeat = mt.accept_followup(note_id, 0, action="accept", database=db)
    assert repeat["changed"] is False                       # 已處理過就不再重複
    with db.session_scope() as session:
        assert session.query(OpenLoop).count() == 1
    with pytest.raises(ValueError):
        mt.accept_followup(note_id, 9, action="accept", database=db)


# ---- 5. 提案與「在開會」 ----


def test_pending_followups_become_one_proposal_per_meeting(transcripts):
    db = TempDatabase()
    _write(transcripts, "m.vtt", VTT)
    mt.build_meeting_notes(
        database=db, cfg=_cfg(transcripts), now=NOW,
        llm_generate=lambda s, u: "摘要：\n- x\n待辦候選：\n- 寄出報告\n- 整理回饋",
    )
    signals, meta = mt.collect_meeting_signals(database=db, cfg=_cfg(transcripts), now=NOW)
    followups = [item for item in signals if item["signal_type"] == "meeting_followups"]
    assert len(followups) == 1 and meta["pending_followups"] == 2
    assert [item["text"] for item in followups[0]["meeting_followups"]] == ["寄出報告", "整理回饋"]

    note_id = followups[0]["meeting_note_id"]
    mt.accept_followup(note_id, 0, action="ignore", database=db)
    mt.accept_followup(note_id, 1, action="ignore", database=db)
    after, meta_after = mt.collect_meeting_signals(database=db, cfg=_cfg(transcripts), now=NOW)
    assert [item for item in after if item["signal_type"] == "meeting_followups"] == []
    assert meta_after["pending_followups"] == 0


def test_missing_transcript_proposal_waits_15_minutes_and_yields_to_a_newer_file(transcripts):
    db = TempDatabase()
    _event(db, "剛結束的會議", start_minutes_ago=90, minutes=60)   # 30 分鐘前結束

    signals, _ = mt.collect_meeting_signals(database=db, cfg=_cfg(transcripts), now=NOW)
    missing = [item for item in signals if item["signal_type"] == "meeting_transcript_missing"]
    assert len(missing) == 1 and "剛結束的會議" in missing[0]["title"]
    assert "30 分鐘前結束" in missing[0]["detail"]

    # 才剛結束（15 分鐘內）先不吵
    early, _ = mt.collect_meeting_signals(
        database=db, cfg=_cfg(transcripts), now=NOW - timedelta(minutes=20)
    )
    assert [item for item in early if item["signal_type"] == "meeting_transcript_missing"] == []

    # 有更新的逐字稿落地就不再提
    _write(transcripts, "m.vtt", VTT, minutes_ago=5)
    after, _ = mt.collect_meeting_signals(database=db, cfg=_cfg(transcripts), now=NOW)
    assert [item for item in after if item["signal_type"] == "meeting_transcript_missing"] == []


def test_in_meeting_needs_both_signals_and_says_which_one_is_missing(transcripts):
    db = TempDatabase()
    cfg = _cfg(transcripts)
    _event(db, "專案會議", start_minutes_ago=10, minutes=60)   # 正在進行

    only_calendar = mt.meeting_context(database=db, cfg=cfg, now=NOW)
    assert only_calendar["in_meeting"] is False
    assert "沒看到會議軟體在前景" in only_calendar["line"]

    with db.session_scope() as session:
        session.add(WindowEvent(
            start_time=NOW - timedelta(minutes=5), end_time=NOW - timedelta(minutes=1),
            duration_seconds=240.0, app_name="ms-teams.exe", window_title="",
            category="Comm",
        ))
    both = mt.meeting_context(database=db, cfg=cfg, now=NOW)
    assert both["in_meeting"] is True and "會議中：專案會議" in both["line"]
    assert both["calendar_event"]["minutes_left"] == 50

    # 只有應用程式、行事曆沒有對應行程
    empty = TempDatabase()
    with empty.session_scope() as session:
        session.add(WindowEvent(
            start_time=NOW - timedelta(minutes=5), end_time=NOW - timedelta(minutes=1),
            duration_seconds=240.0, app_name="Zoom.exe", window_title="", category="Comm",
        ))
    app_only = mt.meeting_context(database=empty, cfg=cfg, now=NOW)
    assert app_only["in_meeting"] is False and "行事曆沒有對應的行程" in app_only["line"]


def test_meeting_notes_template_is_l0_and_registered():
    from core.scheduled_tasks import RISK_L0, SCHEDULABLE_TEMPLATES

    template = SCHEDULABLE_TEMPLATES["meeting_notes"]
    assert template.risk_level == RISK_L0
    assert template.validate_params({"limit": 3}) == {"limit": 3}
    with pytest.raises(Exception):
        template.validate_params({"limit": 99})
