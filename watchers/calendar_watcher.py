"""本機行事曆採集器（ADR-015）：唯讀輪詢 `.ics` 檔，把視野內的行程實例寫進 calendar_events。

與 git_watcher 同形：背景執行緒 ＋ ``check_health_and_heal`` ＋ ``get_diagnostics``。
每個來源檔各自 try/except（壞檔進 ``degraded_sources``，不影響其他檔）；
每次掃描以「檔案 × 視野」整批替換，取消或移動的行程不殘留。

**永不**寫回檔案、**永不**連網、**不**讀 DESCRIPTION／與會者／連結。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config
from core.database import get_db
from core.ics_parser import ParseResult, default_horizon, parse_ics
from core.models import CalendarEvent
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.CalendarWatcher")

MAX_FILE_BYTES = 20 * 1024 * 1024  # 超過就視為異常來源，不讀


def calendar_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    raw_paths = cfg.get("watchers.calendar_watcher.paths", []) or []
    if not isinstance(raw_paths, list):
        raw_paths = []
    paths = [
        Path(os.path.expandvars(os.path.expanduser(str(item)))).resolve()
        for item in raw_paths
        if str(item).strip()
    ]
    try:
        interval = int(cfg.get("watchers.calendar_watcher.scan_interval_seconds", 900))
    except (TypeError, ValueError):
        interval = 900
    try:
        horizon_days = int(cfg.get("watchers.calendar_watcher.horizon_days", 30))
    except (TypeError, ValueError):
        horizon_days = 30
    return {
        "enabled": bool(cfg.get("watchers.calendar_watcher.enabled", True)),
        "paths": paths,
        "scan_interval_seconds": max(60, interval),
        "horizon_days": max(1, min(horizon_days, 366)),
        "store_titles": bool(cfg.get("watchers.calendar_watcher.store_titles", True)),
    }


def calendar_effective(cfg: Any | None = None) -> bool:
    """啟用且至少有一個路徑才算「在採集」；沒路徑就是停用，不報假警報。"""
    settings = calendar_settings(cfg)
    return settings["enabled"] and bool(settings["paths"])


def discover_ics_files(paths: List[Path]) -> List[Path]:
    """檔案直接收；資料夾只收第一層 `.ics`（不遞迴，避免掃到整顆磁碟）。"""
    found: dict[str, Path] = {}
    for path in paths:
        try:
            if path.is_file() and path.suffix.lower() in (".ics", ".ical", ".icalendar"):
                found[str(path)] = path
            elif path.is_dir():
                for child in sorted(path.iterdir()):
                    if child.is_file() and child.suffix.lower() in (".ics", ".ical", ".icalendar"):
                        found[str(child)] = child
        except OSError as exc:
            logger.debug("calendar path unreadable %s: %s", path, type(exc).__name__)
    return list(found.values())


def store_parse_result(
    result: ParseResult,
    *,
    source_path: str,
    database: Any,
    horizon_start: datetime,
    horizon_end: datetime,
    now: datetime,
) -> dict[str, int]:
    """同一交易內：刪掉這個來源在視野內的舊實例，寫入新實例。"""
    calendar_name = result.calendar_name or Path(source_path).stem[:120]
    with database.session_scope() as session:
        removed = (
            session.query(CalendarEvent)
            .filter(
                CalendarEvent.source_path == source_path,
                CalendarEvent.instance_end > horizon_start,
                CalendarEvent.instance_start < horizon_end,
            )
            .delete(synchronize_session=False)
        )
        for inst in result.instances:
            session.add(CalendarEvent(
                uid=inst.uid,
                instance_start=inst.start,
                instance_end=inst.end,
                all_day=inst.all_day,
                summary=inst.summary,
                location=inst.location,
                status=inst.status,
                recurring=inst.recurring,
                calendar_name=calendar_name,
                source_path=source_path,
                last_modified=inst.last_modified,
                last_seen_at=now,
            ))
    return {"removed": int(removed or 0), "written": len(result.instances)}


class CalendarWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._scan_count = 0
        self._last_scan_at: Optional[datetime] = None
        self._last_scan_files = 0
        self._last_scan_instances = 0
        self._degraded_sources: Dict[str, Dict[str, Any]] = {}
        self._source_receipts: Dict[str, Dict[str, Any]] = {}
        self._healing_events: List[Dict[str, Any]] = []

    # ---- 生命週期（與 git_watcher 同形）

    def start(self):
        if not calendar_effective(self.cfg):
            logger.info("Calendar watcher is disabled or has no paths configured.")
            return
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True, name="calendar-watcher")
        self._thread.start()
        logger.info("CalendarWatcher service started.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("CalendarWatcher service stopped.")

    def check_health_and_heal(self) -> Dict[str, Any]:
        if not calendar_effective(self.cfg):
            return {"status": "disabled", "healed": False}
        if self._thread and self._thread.is_alive():
            return {"status": "healthy", "healed": False}
        logger.warning("CalendarWatcher worker thread dead. Initiating self-healing restart...")
        try:
            self._running = True
            self._thread = threading.Thread(target=self._scan_loop, daemon=True, name="calendar-watcher")
            self._thread.start()
            receipt = {
                "timestamp": get_local_now().isoformat(),
                "action": "restart_calendar_worker_thread",
                "status": "success",
            }
            self._healing_events.append(receipt)
            return {"status": "healed", "healed": True, "receipt": receipt}
        except Exception as exc:  # noqa: BLE001
            logger.error("CalendarWatcher self-healing failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc), "healed": False}

    def _scan_loop(self):
        interval = calendar_settings(self.cfg)["scan_interval_seconds"]
        while self._running:
            try:
                self.scan_sources()
            except Exception as exc:  # noqa: BLE001
                logger.error("Error scanning calendars: %s", exc, exc_info=True)
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

    # ---- 掃描

    def scan_sources(self, *, now: datetime | None = None, database: Any | None = None) -> Dict[str, Any]:
        settings = calendar_settings(self.cfg)
        now = now or get_local_now()
        database = database or get_db()
        horizon_start, horizon_end = default_horizon(now, horizon_days=settings["horizon_days"])
        files = discover_ics_files(settings["paths"])
        self._scan_count += 1
        self._last_scan_at = now
        self._last_scan_files = len(files)
        total_instances = 0

        for path in files:
            key = str(path)
            try:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    raise ValueError(f"file_too_large:{size}")
                text = path.read_text(encoding="utf-8", errors="replace")
                result = parse_ics(
                    text,
                    horizon_start=horizon_start,
                    horizon_end=horizon_end,
                    store_titles=settings["store_titles"],
                )
                receipt = store_parse_result(
                    result, source_path=key, database=database,
                    horizon_start=horizon_start, horizon_end=horizon_end, now=now,
                )
                total_instances += receipt["written"]
                self._source_receipts[key] = {
                    "source_name": path.name,
                    "calendar_name": result.calendar_name or path.stem,
                    "vevents": result.vevent_count,
                    "instances_in_horizon": receipt["written"],
                    "replaced": receipt["removed"],
                    "dropped_properties": result.dropped_properties,
                    "warnings": result.warnings[:8],
                    "scanned_at": now.isoformat(timespec="seconds"),
                }
                self._degraded_sources.pop(key, None)
            except Exception as exc:  # noqa: BLE001 — 單一檔案異常不影響其他檔
                self._degraded_sources[key] = {
                    "source_name": path.name,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                    "timestamp": now.isoformat(timespec="seconds"),
                }
                logger.debug("Could not read calendar %s: %s", path, exc)

        # 已從設定移除／消失的來源：把它在資料庫裡的實例清掉，避免幽靈行程
        try:
            with database.session_scope() as session:
                known = {str(p) for p in files}
                stale_sources = [
                    row[0] for row in session.query(CalendarEvent.source_path).distinct().all()
                    if row[0] not in known
                ]
                for source in stale_sources:
                    session.query(CalendarEvent).filter(CalendarEvent.source_path == source).delete(
                        synchronize_session=False
                    )
                    self._source_receipts.pop(source, None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("stale calendar cleanup skipped: %s", type(exc).__name__)

        self._last_scan_instances = total_instances
        return {
            "files": len(files),
            "instances": total_instances,
            "degraded": len(self._degraded_sources),
            "horizon": [horizon_start.isoformat(timespec="seconds"), horizon_end.isoformat(timespec="seconds")],
        }

    # ---- 診斷

    def get_diagnostics(self) -> Dict[str, Any]:
        is_alive = bool(self._thread and self._thread.is_alive())
        settings = calendar_settings(self.cfg)
        return {
            "is_alive": is_alive,
            "state": "running" if is_alive else ("unconfigured" if not settings["paths"] else "stopped"),
            "configured_paths": len(settings["paths"]),
            "store_titles": settings["store_titles"],
            "horizon_days": settings["horizon_days"],
            "scan_count": self._scan_count,
            "last_scan_at": self._last_scan_at.isoformat(timespec="seconds") if self._last_scan_at else None,
            "last_scan_files": self._last_scan_files,
            "last_scan_instances": self._last_scan_instances,
            "sources": list(self._source_receipts.values()),
            "degraded_sources_count": len(self._degraded_sources),
            "degraded_sources": list(self._degraded_sources.values()),
            "healing_events_count": len(self._healing_events),
            "recent_healing_events": self._healing_events[-5:],
            "claim_boundary": "只讀本機 .ics 的時間／標題／地點／狀態；不讀描述與與會者、不寫回、不連網。",
        }
