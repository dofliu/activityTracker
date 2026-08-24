"""在隔離資料庫執行真實 Windows milestone Toast E2E。"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, MilestoneNotificationReceipt, WindowEvent
from core.time_utils import get_local_now
from core.usage_analytics import evaluate_daily_milestones
from notifiers.desktop_notifier import DesktopNotifier


class _Config:
    def __init__(self) -> None:
        self.data = {
            "usage_tracking": {
                "enabled": True,
                "goal_label": "AI 協作 E2E",
                "goal_interfaces": ["Codex"],
                "daily_goal_minutes": 1,
                "milestones_minutes": [1],
                "max_interval_seconds": 600,
                "notifications": {
                    "enabled": True,
                    "quiet_hours_start": "00:00",
                    "quiet_hours_end": "00:00",
                    "cooldown_minutes": 0,
                    "tone": "praise",
                },
            },
            "watchers": {"window_watcher": {"enabled": True}},
        }

    def get(self, key_path: str, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class _Database:
    def __init__(self, path: Path) -> None:
        self.engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
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

    def close(self) -> None:
        self.engine.dispose()


def run_windows_milestone_e2e(output_dir: str | Path | None = None) -> dict:
    """送出真實 WinRT Toast，並驗證 milestone receipt 與重送抑制。"""
    if sys.platform != "win32":
        raise RuntimeError("Windows milestone Toast E2E 只能在 Windows 執行")

    artifact_dir = Path(output_dir).expanduser().resolve() if output_dir else Path(
        tempfile.mkdtemp(prefix="omnicontext-toast-e2e-")
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    database_path = artifact_dir / "milestone-e2e.db"
    database = _Database(database_path)
    cfg = _Config()
    notifier = DesktopNotifier()
    now = get_local_now()

    # 使用當日已過去的兩分鐘，避免測試資料落在未來；凌晨第一分鐘則明確失敗。
    day_start = datetime.combine(now.date(), time.min)
    if now - day_start < timedelta(minutes=2):
        database.close()
        raise RuntimeError("當日尚未經過兩分鐘，無法建立可信的過去時間 E2E interval")
    event_end = now - timedelta(seconds=5)
    event_start = event_end - timedelta(minutes=2)

    with database.session_scope() as session:
        session.add(
            WindowEvent(
                start_time=event_start,
                end_time=event_end,
                duration_seconds=120,
                app_name="Codex.exe",
                window_title="OmniContext Toast E2E - Codex",
                category="AI",
            )
        )

    manager_status = {
        "collector_runtime": {"window_watcher": "running"},
        "collector_health": {"window_watcher": "healthy"},
    }
    result = evaluate_daily_milestones(
        database=database,
        cfg=cfg,
        manager_status=manager_status,
        notifier=notifier,
        now=now,
    )
    repeated = evaluate_daily_milestones(
        database=database,
        cfg=cfg,
        manager_status=manager_status,
        notifier=notifier,
        now=now,
    )
    with database.session_scope() as session:
        rows = session.query(MilestoneNotificationReceipt).all()
        db_receipts = [
            {
                "local_date": row.local_date,
                "milestone_minutes": row.milestone_minutes,
                "channel": row.channel,
                "status": row.status,
                "observed_minutes": row.observed_minutes,
            }
            for row in rows
        ]
    database.close()

    delivery = notifier.last_delivery_receipt or {}
    checks = {
        "real_winrt_transport": delivery.get("transport") == "winrt_toast",
        "os_submission_succeeded": delivery.get("status") == "submitted",
        "milestone_notified": result.get("status") == "notified",
        "database_receipt_written": len(db_receipts) == 1
        and db_receipts[0]["status"] == "sent",
        "duplicate_suppressed": repeated.get("status") == "already_notified",
        "isolated_database": database_path.parent == artifact_dir,
    }
    receipt = {
        "schema": "omnicontext.windows_milestone_toast_e2e.v1",
        "generated_at": get_local_now().astimezone().isoformat(),
        "status": "passed" if all(checks.values()) else "failed",
        "platform": sys.platform,
        "database_path": str(database_path),
        "delivery": delivery,
        "evaluation": {
            "status": result.get("status"),
            "milestone_minutes": result.get("milestone_minutes"),
            "message": result.get("message"),
            "coverage_status": (result.get("summary") or {}).get("coverage_status"),
        },
        "repeat_evaluation_status": repeated.get("status"),
        "database_receipts": db_receipts,
        "checks": checks,
    }
    receipt_path = artifact_dir / "windows-milestone-toast-e2e.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    receipt["receipt_path"] = str(receipt_path)
    if receipt["status"] != "passed":
        raise RuntimeError(json.dumps(receipt, ensure_ascii=False))
    return receipt


if __name__ == "__main__":
    print(json.dumps(run_windows_milestone_e2e(), ensure_ascii=False, indent=2))
