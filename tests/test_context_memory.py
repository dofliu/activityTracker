from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.context_memory import build_recent_work_sessions, find_related_work
from core.models import (
    AIPromptEvent,
    Base,
    FileActivityEvent,
    GitActivityEvent,
    OpenLoop,
)
from core.semantic_index import build_semantic_index


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


class FakeEmbeddingProvider:
    model = "fake-context-v1"

    def embed(self, texts):
        return [
            [1.0, 0.0] if "rollback" in text.lower() else [0.0, 1.0]
            for text in texts
        ]


def _config():
    return DictConfig({
        "semantic_index": {
            "enabled": True,
            "batch_size": 4,
            "max_document_chars": 6000,
            "default_top_k": 6,
            "allow_remote": False,
        }
    })


def _seed(database):
    with database.session_scope() as session:
        session.add_all([
            AIPromptEvent(
                timestamp=datetime(2026, 8, 25, 9, 0),
                platform="codex",
                prompt_text="Plan rollback rehearsal",
                response_text="Use an isolated database backup.",
                project_tag="alpha",
                turn_key="alpha-session-1",
                response_status="final_candidate",
            ),
            FileActivityEvent(
                timestamp=datetime(2026, 8, 25, 9, 20),
                file_path="C:/work/alpha/README.md",
                file_name="README.md",
                file_type=".md",
                action="modified",
                project_name="alpha",
            ),
            GitActivityEvent(
                timestamp=datetime(2026, 8, 25, 10, 20),
                repo_name="alpha",
                repo_path="C:/work/alpha",
                commit_hash="a" * 40,
                message="Document rollback result",
            ),
            AIPromptEvent(
                timestamp=datetime(2026, 8, 25, 9, 10),
                platform="claude",
                prompt_text="Unrelated beta release draft",
                project_tag="beta",
                turn_key="beta-session-1",
                response_status="partial",
            ),
            OpenLoop(
                project_key="alpha",
                title="Verify rollback receipt",
                status="open",
                created_at=datetime(2026, 8, 25, 8, 0),
                last_seen_at=datetime(2026, 8, 25, 10, 0),
            ),
        ])


def test_sessions_group_by_project_and_inactivity_gap_with_stable_provenance(tmp_path):
    database = TempDatabase(tmp_path / "sessions.db")
    _seed(database)

    result = build_recent_work_sessions(
        database=database,
        now=datetime(2026, 8, 25, 11, 0),
        hours=4,
        gap_minutes=45,
        limit=10,
    )

    alpha = [item for item in result["sessions"] if item["project_key"] == "alpha"]
    assert len(alpha) == 2
    earlier = next(item for item in alpha if item["events_observed"] == 2)
    assert earlier["event_counts"] == {"ai_turn": 1, "file_activity": 1}
    assert earlier["items"][0]["source_ref"].startswith("ai_prompt_events:")
    assert earlier["inference_status"] == "temporal_grouping"
    assert result["coverage"]["excluded"] == ["window_focus_without_canonical_project"]
    latest = next(item for item in alpha if item["events_observed"] == 1)
    assert latest["open_loops"][0]["title"] == "Verify rollback receipt"
    assert "do not prove" in result["claim_boundary"]


def test_session_identity_does_not_change_when_the_same_session_grows(tmp_path):
    database = TempDatabase(tmp_path / "stable-session.db")
    _seed(database)
    kwargs = dict(
        database=database,
        now=datetime(2026, 8, 25, 11, 0),
        hours=4,
        gap_minutes=45,
        limit=10,
    )
    before = build_recent_work_sessions(**kwargs)
    target_before = next(
        item for item in before["sessions"]
        if item["project_key"] == "alpha" and item["events_observed"] == 1
    )
    with database.session_scope() as session:
        session.add(FileActivityEvent(
            timestamp=datetime(2026, 8, 25, 10, 35),
            file_path="C:/work/alpha/STATUS.yaml",
            file_name="STATUS.yaml",
            file_type=".yaml",
            action="modified",
            project_name="alpha",
        ))
    after = build_recent_work_sessions(**kwargs)
    target_after = next(
        item for item in after["sessions"]
        if item["project_key"] == "alpha" and item["events_observed"] == 2
    )
    assert target_after["session_id"] == target_before["session_id"]


def test_related_work_is_local_non_persistent_advisory_with_source_boundary(tmp_path):
    database = TempDatabase(tmp_path / "related.db")
    _seed(database)
    provider = FakeEmbeddingProvider()
    build_semantic_index(database=database, cfg=_config(), provider=provider)

    result = find_related_work(
        "How did we run the rollback?",
        database=database,
        cfg=_config(),
        provider=provider,
        project="alpha",
        threshold=0.9,
    )

    assert result["status"] == "related_history_found"
    assert result["query_persisted"] is False
    ai_match = next(
        item for item in result["matches"]
        if item["source_ref"].startswith("ai_prompt_events:")
    )
    assert ai_match["trust_status"] == "final_candidate"
    assert "does not validate truth" in result["claim_boundary"]
