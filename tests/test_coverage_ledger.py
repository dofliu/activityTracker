from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.coverage_ledger import (
    close_open_intervals,
    get_daily_coverage,
    record_observation_heartbeat,
)
from core.models import Base, CoverageLedgerInterval, WindowEvent
from core.usage_analytics import get_usage_summary


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


def _coverage_config(**overrides):
    coverage = {
        "heartbeat_interval_seconds": 60,
        "max_gap_seconds": 180,
        "full_coverage_ratio": 0.95,
    }
    coverage.update(overrides)
    return DictConfig({"usage_tracking": {"enabled": True, "coverage": coverage}})


def _usage_config():
    return DictConfig(
        {
            "usage_tracking": {
                "enabled": True,
                "max_interval_seconds": 10800,
                "goal_label": "Claude + Codex",
                "goal_interfaces": ["Claude", "Codex"],
                "daily_goal_minutes": 60,
                "milestones_minutes": [30, 60],
                "coverage": {
                    "heartbeat_interval_seconds": 60,
                    "max_gap_seconds": 180,
                    "full_coverage_ratio": 0.95,
                },
            },
            "watchers": {"window_watcher": {"enabled": True}},
        }
    )


def _rows(database):
    with database.session_scope() as session:
        return [
            {
                "id": row.id,
                "started_at": row.started_at,
                "last_heartbeat_at": row.last_heartbeat_at,
                "closed_at": row.closed_at,
                "close_reason": row.close_reason,
                "heartbeat_count": row.heartbeat_count,
            }
            for row in session.query(CoverageLedgerInterval)
            .order_by(CoverageLedgerInterval.id)
            .all()
        ]


def test_heartbeat_opens_extends_and_gap_starts_new_interval():
    database = TempDatabase()
    cfg = _coverage_config()
    base = datetime(2026, 8, 30, 9, 0)

    first = record_observation_heartbeat(
        observing=True, database=database, cfg=cfg, now=base
    )
    second = record_observation_heartbeat(
        observing=True, database=database, cfg=cfg, now=base.replace(minute=1)
    )
    # 超過 max_gap（180 秒）的 heartbeat 不得回補中斷區間。
    third = record_observation_heartbeat(
        observing=True, database=database, cfg=cfg, now=base.replace(minute=10)
    )

    assert first["action"] == "opened"
    assert second["action"] == "extended"
    assert third["action"] == "reopened_after_gap"
    rows = _rows(database)
    assert len(rows) == 2
    assert rows[0]["closed_at"] is not None
    assert rows[0]["close_reason"] == "heartbeat_gap"
    # 舊 interval 的結束時間停在最後一次 heartbeat，不是發現中斷的時間。
    assert rows[0]["last_heartbeat_at"] == base.replace(minute=1)
    assert rows[0]["heartbeat_count"] == 2
    assert rows[1]["closed_at"] is None


def test_not_observing_closes_interval_and_stop_closes_all():
    database = TempDatabase()
    cfg = _coverage_config()
    base = datetime(2026, 8, 30, 9, 0)

    record_observation_heartbeat(observing=True, database=database, cfg=cfg, now=base)
    closed = record_observation_heartbeat(
        observing=False,
        database=database,
        cfg=cfg,
        now=base.replace(minute=1),
        reason="window_probe_degraded",
    )
    noop = record_observation_heartbeat(
        observing=False, database=database, cfg=cfg, now=base.replace(minute=2)
    )
    record_observation_heartbeat(
        observing=True, database=database, cfg=cfg, now=base.replace(minute=3)
    )
    stop_receipt = close_open_intervals(
        database=database, now=base.replace(minute=4), reason="monitoring_stopped"
    )

    assert closed["action"] == "closed"
    assert noop["action"] == "noop"
    assert stop_receipt["count"] == 1
    rows = _rows(database)
    assert all(row["closed_at"] is not None for row in rows)
    assert rows[0]["close_reason"] == "window_probe_degraded"
    assert rows[1]["close_reason"] == "monitoring_stopped"


def test_clock_regression_never_extends_backwards():
    database = TempDatabase()
    cfg = _coverage_config()
    base = datetime(2026, 8, 30, 9, 0)

    record_observation_heartbeat(observing=True, database=database, cfg=cfg, now=base)
    receipt = record_observation_heartbeat(
        observing=True, database=database, cfg=cfg, now=base.replace(hour=8)
    )

    assert receipt["action"] == "opened"
    rows = _rows(database)
    assert rows[0]["close_reason"] == "clock_regression"
    assert rows[0]["last_heartbeat_at"] == base


def test_daily_coverage_unions_intervals_against_elapsed_day():
    database = TempDatabase()
    cfg = _coverage_config()
    now = datetime(2026, 8, 30, 12, 0)
    day = datetime(2026, 8, 30, 0, 0)

    with database.session_scope() as session:
        session.add(
            CoverageLedgerInterval(
                collector="window_watcher",
                started_at=day,
                last_heartbeat_at=day.replace(hour=6),
                heartbeat_count=100,
                closed_at=day.replace(hour=6),
                close_reason="heartbeat_gap",
            )
        )
        session.add(
            CoverageLedgerInterval(
                collector="window_watcher",
                started_at=day.replace(hour=5),
                last_heartbeat_at=day.replace(hour=9),
                heartbeat_count=100,
            )
        )

    coverage = get_daily_coverage("2026-08-30", database=database, cfg=cfg, now=now)

    assert coverage["ledger_available"] is True
    # 兩段重疊 interval 聯集為 00:00–09:00 = 32400 秒；已經過 12 小時。
    assert coverage["observed_seconds"] == 32400.0
    assert coverage["elapsed_seconds"] == 43200.0
    assert coverage["coverage_ratio"] == 0.75
    assert coverage["meets_full_coverage"] is False
    assert coverage["interval_count"] == 1
    assert coverage["open_interval"] is True


def test_daily_coverage_without_rows_reports_unavailable_ledger():
    database = TempDatabase()
    coverage = get_daily_coverage(
        "2026-08-30",
        database=database,
        cfg=_coverage_config(),
        now=datetime(2026, 8, 30, 12, 0),
    )
    assert coverage["ledger_available"] is False
    assert coverage["observed_seconds"] == 0.0
    assert coverage["meets_full_coverage"] is False


def test_usage_summary_upgrades_to_observed_when_ledger_meets_threshold(monkeypatch):
    database = TempDatabase()
    cfg = _usage_config()
    now = datetime(2026, 8, 30, 12, 0)
    day = datetime(2026, 8, 30, 0, 0)
    with database.session_scope() as session:
        session.add(
            CoverageLedgerInterval(
                collector="window_watcher",
                started_at=day,
                last_heartbeat_at=day.replace(hour=11, minute=50),
                heartbeat_count=142,
            )
        )
        session.add(
            WindowEvent(
                start_time=day.replace(hour=9),
                end_time=day.replace(hour=10),
                duration_seconds=3600,
                app_name="Codex.exe",
                window_title="Codex",
                category="AI Assistance / Research",
            )
        )

    monkeypatch.setattr("core.usage_analytics.sys.platform", "win32")
    summary = get_usage_summary("2026-08-30", database=database, cfg=cfg, now=now)

    assert summary["coverage_status"] == "observed"
    assert summary["coverage_note"] == "continuous_coverage_ledger"
    assert summary["coverage_ledger"]["meets_full_coverage"] is True
    assert summary["coverage_ledger"]["coverage_ratio"] >= 0.95


def test_usage_summary_reports_ledger_ratio_when_partial(monkeypatch):
    database = TempDatabase()
    cfg = _usage_config()
    now = datetime(2026, 8, 30, 12, 0)
    day = datetime(2026, 8, 30, 0, 0)
    with database.session_scope() as session:
        session.add(
            CoverageLedgerInterval(
                collector="window_watcher",
                started_at=day.replace(hour=9),
                last_heartbeat_at=day.replace(hour=10),
                heartbeat_count=60,
                closed_at=day.replace(hour=10),
                close_reason="monitoring_stopped",
            )
        )

    monkeypatch.setattr("core.usage_analytics.sys.platform", "win32")
    summary = get_usage_summary("2026-08-30", database=database, cfg=cfg, now=now)

    assert summary["coverage_status"] == "partial"
    assert summary["coverage_note"] == "ledger_coverage_8_percent"
    assert summary["coverage_ledger"]["ledger_available"] is True


def test_usage_summary_without_ledger_keeps_existing_partial_note(monkeypatch):
    database = TempDatabase()
    cfg = _usage_config()
    monkeypatch.setattr("core.usage_analytics.sys.platform", "win32")
    summary = get_usage_summary(
        "2026-08-30",
        database=database,
        cfg=cfg,
        now=datetime(2026, 8, 30, 12, 0),
    )
    assert summary["coverage_status"] == "partial"
    assert summary["coverage_note"] == "continuous_coverage_ledger_not_available"

    degraded = get_usage_summary(
        "2026-08-30",
        database=database,
        cfg=cfg,
        now=datetime(2026, 8, 30, 12, 0),
        manager_status={
            "collector_runtime": {"window_watcher": "stopped"},
            "collector_health": {"window_watcher": "stale"},
        },
    )
    assert degraded["coverage_note"] == "collector_stopped_stale"
