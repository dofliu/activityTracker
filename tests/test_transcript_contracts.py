import json
import os
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import AIPromptEvent, Base
from watchers.agent_log_watcher import (
    AgentLogWatcherService,
    build_turn_key,
    classify_response_status,
    eof_response_status,
    iter_jsonl_records,
    select_last_assistant_message,
)


class TempDB:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def test_last_assistant_message_wins_over_commentary():
    messages = ["我正在檢查。", "已完成：最終結果是 B。"]
    assert select_last_assistant_message(messages) == "已完成：最終結果是 B。"


def test_turn_key_depends_on_source_position(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    assert build_turn_key("codex", str(source), 1) == build_turn_key("codex", str(source), 1)
    assert build_turn_key("codex", str(source), 1) != build_turn_key("codex", str(source), 2)


def test_eof_status_is_partial_until_source_settles(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    assert eof_response_status(source, "answer", settle_seconds=120) == "partial"
    old = time.time() - 300
    os.utime(source, (old, old))
    assert eof_response_status(source, "answer", settle_seconds=120) == "final_candidate"
    assert eof_response_status(source, None) == "missing"


def test_explicit_final_marker_is_required_at_active_eof():
    assert classify_response_status("commentary") == "partial"
    assert classify_response_status("answer", explicit_final=True) == "final_candidate"
    assert classify_response_status("answer", boundary_closed=True) == "final_candidate"
    assert classify_response_status(None, explicit_final=True) == "missing"


def test_codex_phase_upgrades_commentary_to_explicit_final(tmp_path):
    source = tmp_path / "rollout.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "s1", "cwd": str(tmp_path)}},
        {
            "type": "response_item",
            "timestamp": "2026-08-24T00:00:00Z",
            "payload": {"role": "user", "content": "question"},
        },
        {
            "type": "response_item",
            "payload": {"role": "assistant", "phase": "commentary", "content": "working update"},
        },
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    db = TempDB()
    service = AgentLogWatcherService()
    service._parse_codex_jsonl_session(db, source)
    with db.session_scope() as session:
        event = session.query(AIPromptEvent).one()
        assert event.response_status == "partial"
        assert event.response_text == "working update"

    rows.append(
        {
            "type": "response_item",
            "payload": {"role": "assistant", "phase": "final_answer", "content": "final result"},
        }
    )
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    service._parse_codex_jsonl_session(db, source)
    with db.session_scope() as session:
        event = session.query(AIPromptEvent).one()
        assert event.response_status == "final_candidate"
        assert event.response_text == "final result"


def test_malformed_jsonl_is_not_silently_accepted(tmp_path):
    source = tmp_path / "broken.jsonl"
    source.write_text(json.dumps({"ok": 1}) + "\n{broken\n", encoding="utf-8")
    iterator = iter_jsonl_records(source)
    assert next(iterator)[1] == {"ok": 1}
    with pytest.raises(ValueError, match="Malformed JSONL"):
        list(iterator)


def test_claude_desktop_project_log_preserves_provenance_and_is_idempotent(tmp_path):
    source = tmp_path / "local-agent-mode-sessions" / "w" / "s" / "local_test" / ".claude" / "projects" / "demo" / "session.jsonl"
    source.parent.mkdir(parents=True)
    rows = [
        {
            "type": "user",
            "timestamp": "2026-08-25T01:00:00Z",
            "sessionId": "desktop-session",
            "cwd": str(tmp_path / "project"),
            "message": {"content": "請檢查桌面專案"},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "已完成桌面檢查"}], "stop_reason": "end_turn"},
        },
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    database = TempDB()
    service = AgentLogWatcherService()
    service._parse_claude_project_log(database, source, platform="claude_desktop")
    service._parse_claude_project_log(database, source, platform="claude_desktop")

    with database.session_scope() as session:
        events = session.query(AIPromptEvent).all()
        assert len(events) == 1
        event = events[0]
        assert event.platform == "claude_desktop"
        assert event.conversation_id == "desktop-session"
        assert event.prompt_text == "請檢查桌面專案"
        assert event.response_text == "已完成桌面檢查"
        assert event.response_status == "final_candidate"
        assert event.source_path == str(source.resolve())
        assert event.source_position == 1


def test_agent_source_failure_does_not_block_remaining_sources(monkeypatch):
    service = AgentLogWatcherService()
    calls = []

    monkeypatch.setattr(service.cfg, "get", lambda key, default=None: True)
    monkeypatch.setattr(
        service,
        "scan_claude_code_logs",
        lambda full_history=False: calls.append("claude_code"),
    )

    def fail_claude_desktop(full_history=False):
        calls.append("claude_desktop")
        raise PermissionError("access denied")

    monkeypatch.setattr(service, "scan_claude_desktop_logs", fail_claude_desktop)
    monkeypatch.setattr(
        service,
        "scan_codex_logs",
        lambda full_history=False: calls.append("codex"),
    )
    monkeypatch.setattr(
        service,
        "scan_antigravity_logs",
        lambda full_history=False: calls.append("antigravity"),
    )

    service.scan_all_agents(full_history=False)

    assert calls == ["claude_code", "claude_desktop", "codex", "antigravity"]
    diagnostics = service.get_diagnostics()
    assert diagnostics["state"] == "degraded"
    assert diagnostics["sources"]["claude_desktop"]["state"] == "error"
    assert diagnostics["sources"]["claude_desktop"]["last_error_code"] == "permission_denied"
    assert diagnostics["sources"]["claude_desktop"]["consecutive_errors"] == 1
    assert diagnostics["sources"]["codex"]["state"] == "healthy"
    assert "access denied" not in str(diagnostics).lower()
