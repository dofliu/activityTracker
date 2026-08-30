"""P2.6 continuous coverage ledger：只記錄採集器被觀測為運作中的時間段。

heartbeat 由排程器週期性寫入；interval 的結束時間永遠是最後一次
heartbeat，因此程序中斷、休眠或當機都不會把中斷後的時間補進 coverage。
ledger 證明的是「採集器何時在觀測」，不是使用者在場、機器開機時間
或事件完整性。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from core.config import get_config
from core.database import get_db
from core.models import CoverageLedgerInterval
from core.time_utils import get_local_now


WINDOW_COLLECTOR = "window_watcher"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 300
DEFAULT_FULL_COVERAGE_RATIO = 0.95

LEDGER_CLAIM_BOUNDARY = (
    "Ledger proves observed collector runtime only; it is not user presence, "
    "machine uptime, or event completeness."
)


def heartbeat_interval_seconds(cfg: Any | None = None) -> int:
    cfg = cfg or get_config()
    raw = cfg.get(
        "usage_tracking.coverage.heartbeat_interval_seconds",
        DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    )
    try:
        return min(3600, max(30, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def max_heartbeat_gap_seconds(cfg: Any | None = None) -> int:
    """超過此間隔的 heartbeat 視為中斷：舊 interval 關閉、開新 interval。"""
    cfg = cfg or get_config()
    interval = heartbeat_interval_seconds(cfg)
    raw = cfg.get("usage_tracking.coverage.max_gap_seconds", interval * 3)
    try:
        return max(interval, int(raw))
    except (TypeError, ValueError):
        return interval * 3


def full_coverage_ratio(cfg: Any | None = None) -> float:
    cfg = cfg or get_config()
    raw = cfg.get("usage_tracking.coverage.full_coverage_ratio", DEFAULT_FULL_COVERAGE_RATIO)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_FULL_COVERAGE_RATIO
    return min(1.0, max(0.5, value))


def _open_interval(session, collector: str) -> CoverageLedgerInterval | None:
    return (
        session.query(CoverageLedgerInterval)
        .filter(
            CoverageLedgerInterval.collector == collector,
            CoverageLedgerInterval.closed_at.is_(None),
        )
        .order_by(
            CoverageLedgerInterval.started_at.desc(),
            CoverageLedgerInterval.id.desc(),
        )
        .first()
    )


def record_observation_heartbeat(
    collector: str = WINDOW_COLLECTOR,
    *,
    observing: bool,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """``observing=True`` 開啟或延長 interval；``False`` 關閉既有 interval。

    只有「此刻觀測到採集器運作」才延長 coverage；任何無法確認的狀態
    都以關閉處理，寧可少算不多算。
    """
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    max_gap = timedelta(seconds=max_heartbeat_gap_seconds(cfg))

    with database.session_scope() as session:
        row = _open_interval(session, collector)

        if not observing:
            if row is None:
                return {"collector": collector, "action": "noop", "reason": reason}
            row.closed_at = now
            row.close_reason = (reason or "not_observing")[:40]
            return {
                "collector": collector,
                "action": "closed",
                "interval_id": row.id,
                "observed_until": row.last_heartbeat_at.isoformat(timespec="seconds"),
                "reason": reason,
            }

        if row is not None and now < row.last_heartbeat_at:
            # 時鐘倒退（手動調整或 DST）：不延長既有 interval，保守重開。
            row.closed_at = row.last_heartbeat_at
            row.close_reason = "clock_regression"
            row = None

        if row is not None and now - row.last_heartbeat_at <= max_gap:
            row.last_heartbeat_at = now
            row.heartbeat_count = int(row.heartbeat_count or 0) + 1
            return {"collector": collector, "action": "extended", "interval_id": row.id}

        action = "opened"
        if row is not None:
            row.closed_at = row.last_heartbeat_at
            row.close_reason = "heartbeat_gap"
            action = "reopened_after_gap"
        fresh = CoverageLedgerInterval(
            collector=collector,
            started_at=now,
            last_heartbeat_at=now,
            heartbeat_count=1,
        )
        session.add(fresh)
        session.flush()
        return {"collector": collector, "action": action, "interval_id": fresh.id}


def close_open_intervals(
    collector: str | None = None,
    *,
    reason: str = "monitoring_stopped",
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """優雅停止時關閉 open interval；結束時間維持最後一次 heartbeat。"""
    database = database or get_db()
    now = now or get_local_now()
    with database.session_scope() as session:
        query = session.query(CoverageLedgerInterval).filter(
            CoverageLedgerInterval.closed_at.is_(None)
        )
        if collector:
            query = query.filter(CoverageLedgerInterval.collector == collector)
        rows = query.all()
        for row in rows:
            row.closed_at = now
            row.close_reason = reason[:40]
        return {"action": "closed", "count": len(rows), "reason": reason}


def _parse_target_date(value: date | str | None, *, default_date: date) -> date:
    if value is None:
        return default_date
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def get_daily_coverage(
    target_date: date | str | None = None,
    *,
    collector: str = WINDOW_COLLECTOR,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """回傳指定日期的 ledger coverage；分母為該日已經過的 wall-clock 時間。"""
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    selected_date = _parse_target_date(target_date, default_date=now.date())
    day_start = datetime.combine(selected_date, time.min)
    day_end = day_start + timedelta(days=1)
    window_end = min(day_end, max(day_start, now))
    elapsed_seconds = max(0.0, (window_end - day_start).total_seconds())

    with database.session_scope() as session:
        rows = (
            session.query(CoverageLedgerInterval)
            .filter(
                CoverageLedgerInterval.collector == collector,
                CoverageLedgerInterval.started_at < day_end,
                CoverageLedgerInterval.last_heartbeat_at > day_start,
            )
            .order_by(CoverageLedgerInterval.started_at.asc())
            .all()
        )
        clipped: list[tuple[datetime, datetime]] = []
        has_open_interval = False
        heartbeats = 0
        for row in rows:
            if row.closed_at is None:
                has_open_interval = True
            heartbeats += int(row.heartbeat_count or 0)
            start = max(row.started_at, day_start)
            end = min(row.last_heartbeat_at, window_end)
            if end > start:
                clipped.append((start, end))

    merged = _merge_intervals(clipped)
    observed_seconds = sum((end - start).total_seconds() for start, end in merged)
    ratio = (observed_seconds / elapsed_seconds) if elapsed_seconds > 0 else 0.0
    threshold = full_coverage_ratio(cfg)

    return {
        "collector": collector,
        "date": selected_date.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "ledger_available": bool(rows),
        "interval_count": len(merged),
        "heartbeat_count": heartbeats,
        "observed_seconds": round(observed_seconds, 3),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "coverage_ratio": round(ratio, 4),
        "full_coverage_ratio_threshold": threshold,
        "meets_full_coverage": bool(rows) and ratio >= threshold,
        "first_observed_at": (
            merged[0][0].isoformat(timespec="seconds") if merged else None
        ),
        "last_observed_at": (
            merged[-1][1].isoformat(timespec="seconds") if merged else None
        ),
        "open_interval": has_open_interval,
        "claim_boundary": LEDGER_CLAIM_BOUNDARY,
    }
