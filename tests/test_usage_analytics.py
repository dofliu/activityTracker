from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, MilestoneNotificationReceipt, WindowEvent
from core.usage_analytics import (
    aggregate_window_events,
    classify_interface,
    evaluate_daily_milestones,
    get_usage_summary,
    is_quiet_hours,
)


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


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send_usage_milestone(self, summary, milestone_minutes, message):
        self.messages.append((summary["date"], milestone_minutes, message))
        return True


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
                "notifications": {
                    "enabled": True,
                    "tone": "praise",
                    "quiet_hours_start": "22:00",
                    "quiet_hours_end": "08:00",
                },
            },
            "watchers": {"window_watcher": {"enabled": True}},
        }
    )


def test_title_rule_wins_over_generic_process_rule():
    assert classify_interface("ChatGPT.exe", "Codex — activityTracker") == "Codex"


def test_overlap_and_exact_duplicate_never_double_count():
    start = datetime(2026, 8, 24, 9, 0)
    end = datetime(2026, 8, 24, 11, 0)
    events = [
        {
            "id": 1,
            "start_time": start,
            "end_time": datetime(2026, 8, 24, 10, 0),
            "app_name": "ChatGPT.exe",
            "window_title": "ChatGPT",
        },
        {
            "id": 2,
            "start_time": datetime(2026, 8, 24, 9, 30),
            "end_time": datetime(2026, 8, 24, 10, 30),
            "app_name": "Codex.exe",
            "window_title": "Codex",
        },
        {
            "id": 3,
            "start_time": datetime(2026, 8, 24, 9, 30),
            "end_time": datetime(2026, 8, 24, 10, 30),
            "app_name": "Codex.exe",
            "window_title": "Codex",
        },
    ]

    result = aggregate_window_events(events, start, end, max_interval_seconds=7200)

    assert result["ChatGPT"]["foreground_seconds"] == 30 * 60
    assert result["Codex"]["foreground_seconds"] == 60 * 60
    assert sum(item["foreground_seconds"] for item in result.values()) == 90 * 60
    assert result["Codex"]["event_count"] == 1


def test_cross_midnight_interval_is_clipped_to_requested_day():
    day_start = datetime(2026, 8, 24, 0, 0)
    day_end = datetime(2026, 8, 25, 0, 0)
    event = {
        "id": 1,
        "start_time": datetime(2026, 8, 23, 23, 45),
        "end_time": datetime(2026, 8, 24, 0, 15),
        "app_name": "claude.exe",
        "window_title": "Claude",
    }
    result = aggregate_window_events([event], day_start, day_end)
    assert result["Claude"]["foreground_seconds"] == 15 * 60


def test_quiet_hours_supports_range_across_midnight():
    assert is_quiet_hours(datetime(2026, 8, 24, 23, 0), "22:00", "08:00")
    assert is_quiet_hours(datetime(2026, 8, 24, 7, 59), "22:00", "08:00")
    assert not is_quiet_hours(datetime(2026, 8, 24, 12, 0), "22:00", "08:00")


def test_unsupported_platform_reports_unavailable_not_zero_coverage(tmp_path, monkeypatch):
    database = TempDatabase(tmp_path / "unsupported.db")
    cfg = _usage_config()
    monkeypatch.setattr("core.usage_analytics.sys.platform", "linux")
    summary = get_usage_summary(
        "2026-08-24",
        database=database,
        cfg=cfg,
        now=datetime(2026, 8, 24, 12, 0),
    )
    assert summary["coverage_status"] == "unavailable"
    assert summary["coverage_note"] == "window_collector_not_supported_on_platform"


def test_milestone_receipt_is_idempotent_and_coalesces_lower_thresholds(tmp_path, monkeypatch):
    database = TempDatabase(tmp_path / "usage.db")
    cfg = _usage_config()
    now = datetime(2026, 8, 24, 12, 0)
    with database.session_scope() as session:
        session.add(
            WindowEvent(
                start_time=datetime(2026, 8, 24, 9, 0),
                end_time=datetime(2026, 8, 24, 10, 15),
                duration_seconds=4500,
                app_name="Codex.exe",
                window_title="Codex",
                category="AI Assistance / Research",
            )
        )

    monkeypatch.setattr("core.usage_analytics.sys.platform", "win32")
    summary = get_usage_summary("2026-08-24", database=database, cfg=cfg, now=now)
    assert summary["coverage_status"] == "partial"
    assert summary["goal"]["foreground_minutes"] == 75.0

    notifier = FakeNotifier()
    first = evaluate_daily_milestones(
        database=database, cfg=cfg, notifier=notifier, now=now
    )
    second = evaluate_daily_milestones(
        database=database, cfg=cfg, notifier=notifier, now=now
    )

    assert first["status"] == "notified"
    assert first["milestone_minutes"] == 60
    assert first["coalesced_milestones"] == [30]
    assert "已記錄至少" in first["message"]
    assert second["status"] == "already_notified"
    assert len(notifier.messages) == 1
    with database.session_scope() as session:
        receipts = session.query(MilestoneNotificationReceipt).all()
        assert {row.status for row in receipts} == {"sent", "coalesced"}


def test_new_milestone_waits_for_configured_cooldown(tmp_path, monkeypatch):
    database = TempDatabase(tmp_path / "cooldown.db")
    cfg = _usage_config()
    cfg.data["usage_tracking"]["milestones_minutes"] = [30, 60, 90]
    cfg.data["usage_tracking"]["notifications"]["cooldown_minutes"] = 60
    with database.session_scope() as session:
        session.add(
            WindowEvent(
                start_time=datetime(2026, 8, 24, 9, 0),
                end_time=datetime(2026, 8, 24, 10, 15),
                duration_seconds=4500,
                app_name="Codex.exe",
                window_title="Codex",
                category="AI Assistance / Research",
            )
        )

    monkeypatch.setattr("core.usage_analytics.sys.platform", "win32")
    notifier = FakeNotifier()
    first = evaluate_daily_milestones(
        database=database,
        cfg=cfg,
        notifier=notifier,
        now=datetime(2026, 8, 24, 12, 0),
    )
    assert first["milestone_minutes"] == 60

    with database.session_scope() as session:
        session.add(
            WindowEvent(
                start_time=datetime(2026, 8, 24, 12, 0),
                end_time=datetime(2026, 8, 24, 12, 30),
                duration_seconds=1800,
                app_name="claude.exe",
                window_title="Claude",
                category="AI Assistance / Research",
            )
        )

    cooldown = evaluate_daily_milestones(
        database=database,
        cfg=cfg,
        notifier=notifier,
        now=datetime(2026, 8, 24, 12, 30),
    )
    after_cooldown = evaluate_daily_milestones(
        database=database,
        cfg=cfg,
        notifier=notifier,
        now=datetime(2026, 8, 24, 13, 1),
    )

    assert cooldown["status"] == "cooldown"
    assert cooldown["pending_milestones"] == [90]
    assert after_cooldown["status"] == "notified"
    assert after_cooldown["milestone_minutes"] == 90
    assert len(notifier.messages) == 2
