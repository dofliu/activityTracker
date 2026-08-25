from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, OpenLoop, ProjectState
from core.proactive_secretary import build_action_proposals


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
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
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


def _config(enabled=True):
    return DictConfig({
        "proactive_secretary": {
            "enabled": enabled,
            "max_proposals": 6,
            "stalled_open_loop_hours": 48,
        }
    })


def _extension_status(heartbeat_verified=False):
    return {
        "extension": {
            "token_configured": True,
            "heartbeat_verified": heartbeat_verified,
        }
    }


def test_proposals_are_stable_traceable_and_never_executable(tmp_path):
    database = TempDatabase(tmp_path / "proposals.db")
    with database.session_scope() as session:
        session.add(
            ProjectState(
                project_key="alpha",
                display_name="Alpha",
                category="Coding",
                last_activity_at=datetime(2026, 8, 20, 9, 0),
                status="idle",
            )
        )
        session.add_all([
            OpenLoop(
                project_key="alpha",
                title="sensitive prompt body must not leak",
                status="open",
                created_at=datetime(2026, 8, 20, 10, 0),
                last_seen_at=datetime(2026, 8, 20, 10, 0),
            ),
            OpenLoop(
                project_key="alpha",
                title="stale item must not become actionable",
                status="stale",
                created_at=datetime(2026, 8, 19, 10, 0),
                last_seen_at=datetime(2026, 8, 19, 10, 0),
            ),
        ])

    kwargs = {
        "database": database,
        "cfg": _config(),
        "now": datetime(2026, 8, 26, 10, 0),
        "extension_status": _extension_status(),
    }
    first = build_action_proposals(**kwargs)
    second = build_action_proposals(**kwargs)

    assert first == second
    assert first["mode"] == "proposal_only"
    assert first["execution_available"] is False
    assert first["cloud_llm_used"] is False
    assert first["query_persisted"] is False
    assert len(first["proposals"]) == 2
    assert all(item["execution_available"] is False for item in first["proposals"])
    assert all(item["evidence_refs"] for item in first["proposals"])
    assert any(
        ref.startswith("open_loops:")
        for item in first["proposals"]
        for ref in item["evidence_refs"]
    )
    serialized = str(first)
    assert "sensitive prompt body" not in serialized
    assert "stale item" not in serialized
    assert "command" not in serialized.lower()


def test_recent_project_and_verified_extension_produce_no_proposal(tmp_path):
    database = TempDatabase(tmp_path / "recent.db")
    with database.session_scope() as session:
        session.add(
            ProjectState(
                project_key="recent",
                display_name="Recent",
                last_activity_at=datetime(2026, 8, 26, 9, 30),
                status="active",
            )
        )
        session.add(
            OpenLoop(
                project_key="recent",
                title="review later",
                status="open",
                created_at=datetime(2026, 8, 26, 9, 0),
            )
        )

    result = build_action_proposals(
        database=database,
        cfg=_config(),
        now=datetime(2026, 8, 26, 10, 0),
        extension_status=_extension_status(heartbeat_verified=True),
    )
    assert result["proposals"] == []


def test_disabled_proposal_engine_is_explicit(tmp_path):
    database = TempDatabase(tmp_path / "disabled.db")
    result = build_action_proposals(
        database=database,
        cfg=_config(enabled=False),
        now=datetime(2026, 8, 26, 10, 0),
        extension_status=_extension_status(),
    )
    assert result["status"] == "disabled"
    assert result["proposals"] == []
    assert result["mode"] == "proposal_only"
    assert result["execution_available"] is False
    assert result["cloud_llm_used"] is False
    assert result["query_persisted"] is False
