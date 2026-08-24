from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import watchers.agent_log_watcher as watcher_module
from core.models import Base, IngestionCheckpoint
from watchers.agent_log_watcher import AgentLogWatcherService


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


def test_checkpoint_error_does_not_advance_signature(monkeypatch, tmp_path):
    db = TempDB()
    monkeypatch.setattr(watcher_module, "get_db", lambda: db)
    service = AgentLogWatcherService()
    source = tmp_path / "source.jsonl"
    source.write_text("{}\n", encoding="utf-8")

    service._mark_file_scanned(source)
    with db.session_scope() as session:
        first = session.query(IngestionCheckpoint).one()
        original_signature = (first.mtime_ns, first.size_bytes)

    source.write_text("{}\n{broken\n", encoding="utf-8")
    service._mark_file_scanned(source, "Malformed JSONL")
    with db.session_scope() as session:
        failed = session.query(IngestionCheckpoint).one()
        assert (failed.mtime_ns, failed.size_bytes) == original_signature
        assert failed.last_error == "Malformed JSONL"
    assert service._should_scan_file(source, full_history=False)
