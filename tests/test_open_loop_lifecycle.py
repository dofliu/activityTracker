from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.project_engine as project_engine
from core.models import Base


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


def test_open_loop_state_machine_and_actionable_filter(monkeypatch):
    db = TempDB()
    monkeypatch.setattr(project_engine, "get_db", lambda: db)

    loop_id = project_engine.create_open_loop("Demo", "Verify release gate")
    assert project_engine.create_open_loop("demo", " verify   release gate ") == loop_id
    assert [item["id"] for item in project_engine.get_open_loops_list()] == [loop_id]

    project_engine.transition_open_loop(loop_id, "stale", "needs review")
    assert project_engine.get_open_loops_list() == []
    assert project_engine.get_open_loops_list(statuses={"stale"})[0]["id"] == loop_id

    project_engine.transition_open_loop(loop_id, "resolved", "done")
    assert project_engine.get_open_loops_list() == []
    project_engine.transition_open_loop(loop_id, "open", "reopened")
    assert project_engine.get_open_loops_list()[0]["id"] == loop_id
