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
