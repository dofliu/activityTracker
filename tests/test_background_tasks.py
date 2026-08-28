from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.background_tasks import (
    BackgroundTaskEvidence,
    get_background_task_summary,
    record_background_task_evidence,
)
from core.models import BackgroundTaskRun, Base


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


def _config(maximum=8 * 60 * 60):
    return DictConfig(
        {
            "background_task_tracking": {
                "enabled": True,
                "platforms": ["codex", "claude_code", "claude_desktop"],
                "max_task_duration_seconds": maximum,
            }
        }
    )


def _evidence(*, platform="codex", started_at, completed_at=None, position=10):
    return BackgroundTaskEvidence(
        platform=platform,
        source_path="C:/sessions/example.jsonl",
        started_at=started_at,
        start_position=position,
        session_id="session-1",
        cwd="C:/work/activityTracker",
        completed_at=completed_at,
        end_position=position + 2 if completed_at else None,
        completion_evidence_kind="codex_final_answer" if completed_at else None,
    )


def test_only_paired_receipts_contribute_to_verified_duration():
    database = TempDatabase()
    cfg = _config()
    started_at = datetime(2026, 8, 29, 9, 0)

    record_background_task_evidence(
        _evidence(started_at=started_at), database=database, cfg=cfg
    )
    pending = get_background_task_summary(
        "2026-08-29", database=database, cfg=cfg, now=datetime(2026, 8, 29, 12, 0)
    )
    assert pending["verified_seconds"] == 0
    assert pending["awaiting_final_count"] == 1
    assert pending["evidence_status"] == "not_observed"

    record_background_task_evidence(
        _evidence(started_at=started_at, completed_at=datetime(2026, 8, 29, 9, 15)),
        database=database,
        cfg=cfg,
    )
    completed = get_background_task_summary(
        "2026-08-29", database=database, cfg=cfg, now=datetime(2026, 8, 29, 12, 0)
    )
    assert completed["verified_seconds"] == 15 * 60
    assert completed["completed_task_count"] == 1
    assert completed["interfaces"] == [
        {"platform": "codex", "label": "Codex", "verified_seconds": 15 * 60, "completed_tasks": 1}
    ]
    with database.session_scope() as session:
        assert session.query(BackgroundTaskRun).count() == 1


def test_parallel_background_tasks_use_union_duration_for_total():
    database = TempDatabase()
    cfg = _config()
    record_background_task_evidence(
        _evidence(started_at=datetime(2026, 8, 29, 9, 0), completed_at=datetime(2026, 8, 29, 10, 0)),
        database=database,
        cfg=cfg,
    )
    record_background_task_evidence(
        _evidence(
            platform="claude_code",
            started_at=datetime(2026, 8, 29, 9, 30),
            completed_at=datetime(2026, 8, 29, 10, 30),
            position=20,
        ),
        database=database,
        cfg=cfg,
    )

    summary = get_background_task_summary(
        "2026-08-29", database=database, cfg=cfg, now=datetime(2026, 8, 29, 12, 0)
    )
    assert summary["verified_seconds"] == 90 * 60
    assert {item["label"]: item["verified_seconds"] for item in summary["interfaces"]} == {
        "Codex": 60 * 60,
        "Claude Code": 60 * 60,
    }


def test_out_of_bounds_duration_is_preserved_but_not_counted():
    database = TempDatabase()
    cfg = _config(maximum=60 * 60)
    record_background_task_evidence(
        _evidence(started_at=datetime(2026, 8, 29, 9, 0), completed_at=datetime(2026, 8, 29, 11, 0)),
        database=database,
        cfg=cfg,
    )
    summary = get_background_task_summary(
        "2026-08-29", database=database, cfg=cfg, now=datetime(2026, 8, 29, 12, 0)
    )
    assert summary["verified_seconds"] == 0
    assert summary["untrusted_duration_count"] == 1
