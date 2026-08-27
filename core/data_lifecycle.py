"""SQLite data lifecycle operations with verifiable, non-destructive backups."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_config
from core.runtime_paths import resolve_runtime_path


def configured_database_path() -> Path:
    cfg = get_config()
    return resolve_runtime_path(cfg.get("database.db_path", "omni_context.db"))


def configured_backup_dir() -> Path:
    cfg = get_config()
    return resolve_runtime_path(
        cfg.get("data_lifecycle.backups_dir", "~/OmniContext/backups")
    )


def verify_sqlite_database(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    with closing(sqlite3.connect(path)) as connection:
        integrity = connection.execute("PRAGMA integrity_check;").fetchone()[0]
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_names = [row[0] for row in tables]
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    return {
        "path": str(path),
        "integrity": integrity,
        "table_count": len(table_names),
        "tables": table_names,
        "size_bytes": path.stat().st_size,
        "sha256": digest,
    }


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    """Restore drill 僅讀取既有備份，避免驗證流程改寫來源檔。"""
    return sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)


def _database_logical_contract(db_path: str | Path) -> dict[str, Any]:
    """回傳不含資料內容的 schema fingerprint 與各表 row counts。"""
    path = Path(db_path).resolve()
    with closing(_read_only_connection(path)) as connection:
        schema_rows = connection.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '')
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        tables = [
            row[1]
            for row in schema_rows
            if row[0] == "table"
        ]
        row_counts = {}
        for table_name in tables:
            quoted = table_name.replace('"', '""')
            row_counts[table_name] = connection.execute(
                f'SELECT COUNT(*) FROM "{quoted}"'
            ).fetchone()[0]

    schema_payload = json.dumps(
        schema_rows,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_sha256": hashlib.sha256(schema_payload).hexdigest(),
        "row_counts": row_counts,
    }


def backup_sqlite_database(
    source_path: str | Path,
    destination_dir: str | Path,
) -> dict[str, Any]:
    """使用 SQLite Online Backup API，避免直接複製 WAL 中的活動資料庫。"""
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source database not found: {source}")
    destination_root = Path(destination_dir).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = destination_root / f"{source.stem}-{timestamp}.db"

    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)

    receipt = verify_sqlite_database(destination)
    if receipt["integrity"] != "ok":
        raise RuntimeError(f"Backup integrity check failed: {receipt['integrity']}")
    receipt["source_path"] = str(source)
    return receipt


def create_configured_backup(destination_dir: str | Path | None = None) -> dict[str, Any]:
    return backup_sqlite_database(
        configured_database_path(),
        destination_dir or configured_backup_dir(),
    )


def latest_backup_path(backup_dir: str | Path | None = None) -> Path:
    root = Path(backup_dir or configured_backup_dir()).expanduser().resolve()
    candidates = sorted(
        (path for path in root.glob("*.db") if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No SQLite backup found in: {root}")
    return candidates[0]


def restore_drill(
    backup_path: str | Path,
    receipt_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    將備份還原到隔離暫存資料庫並比對 logical contract。

    此函式不接受 live database destination，也不會覆寫任何既有資料庫。
    暫存還原檔在驗證後自動移除，只保留不含 row content 的 JSON receipt。
    """
    source = Path(backup_path).expanduser().resolve()
    source_verification = verify_sqlite_database(source)
    if source_verification["integrity"] != "ok":
        raise RuntimeError(
            f"Source backup integrity check failed: {source_verification['integrity']}"
        )

    source_contract = _database_logical_contract(source)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    receipt_root = Path(
        receipt_dir or (source.parent / "restore_drills")
    ).expanduser().resolve()
    receipt_root.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_root / f"restore-drill-{timestamp}.json"

    with tempfile.TemporaryDirectory(prefix="omnicontext-restore-drill-") as temp_root:
        restored_path = Path(temp_root) / "restored.db"
        with closing(_read_only_connection(source)) as source_connection:
            with closing(sqlite3.connect(restored_path)) as restored_connection:
                source_connection.backup(restored_connection)

        restored_verification = verify_sqlite_database(restored_path)
        restored_contract = _database_logical_contract(restored_path)
        checks = {
            "integrity_ok": restored_verification["integrity"] == "ok",
            "table_list_match": (
                restored_verification["tables"] == source_verification["tables"]
            ),
            "schema_match": (
                restored_contract["schema_sha256"]
                == source_contract["schema_sha256"]
            ),
            "row_counts_match": (
                restored_contract["row_counts"] == source_contract["row_counts"]
            ),
        }
        passed = all(checks.values())
        receipt = {
            "receipt_version": 1,
            "operation": "restore_drill",
            "status": "passed" if passed else "failed",
            "created_at": datetime.now().astimezone().isoformat(),
            "source_backup": {
                "path": str(source),
                "sha256": source_verification["sha256"],
                "size_bytes": source_verification["size_bytes"],
                "table_count": source_verification["table_count"],
                "schema_sha256": source_contract["schema_sha256"],
                "row_counts": source_contract["row_counts"],
            },
            "isolated_restore": {
                "integrity": restored_verification["integrity"],
                "table_count": restored_verification["table_count"],
                "schema_sha256": restored_contract["schema_sha256"],
                "temporary_copy_retained": False,
            },
            "checks": checks,
        }

    temporary_receipt = receipt_path.with_suffix(".json.tmp")
    temporary_receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_receipt.replace(receipt_path)
    receipt["receipt_path"] = str(receipt_path)

    if receipt["status"] != "passed":
        raise RuntimeError(f"Restore drill failed; receipt: {receipt_path}")
    return receipt


def run_configured_restore_drill(
    backup_path: str | Path | None = None,
    receipt_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = (
        Path(backup_path).expanduser().resolve()
        if backup_path
        else latest_backup_path()
    )
    return restore_drill(source, receipt_dir)


def checkpoint_sqlite_database(
    db_path: str | Path | None = None,
    mode: str = "TRUNCATE",
) -> dict[str, Any]:
    """
    執行 SQLite WAL Checkpoint，將 WAL 內容同步回主庫並截斷 WAL 檔案。
    模式支援 PASSIVE, FULL, RESTART, TRUNCATE（預設 TRUNCATE）。
    """
    path = Path(db_path or configured_database_path()).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    wal_path = Path(f"{path}-wal")
    wal_size_before = wal_path.stat().st_size if wal_path.exists() else 0

    valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
    chk_mode = mode.upper() if mode.upper() in valid_modes else "TRUNCATE"

    with closing(sqlite3.connect(path, timeout=30)) as conn:
        cursor = conn.cursor()
        result = cursor.execute(f"PRAGMA wal_checkpoint({chk_mode});").fetchone()
        busy, log_pages, checkpointed_pages = result[0], result[1], result[2]

    wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0

    return {
        "operation": "wal_checkpoint",
        "mode": chk_mode,
        "database": str(path),
        "busy": bool(busy),
        "log_pages": log_pages,
        "checkpointed_pages": checkpointed_pages,
        "wal_size_bytes_before": wal_size_before,
        "wal_size_bytes_after": wal_size_after,
        "timestamp": datetime.now().astimezone().isoformat(),
    }


def rotate_backups(
    backup_dir: str | Path | None = None,
    max_backups: int = 7,
) -> dict[str, Any]:
    """
    滾動保留最新 max_backups 份備份檔案，自動清理過期的舊備份。
    """
    root = Path(backup_dir or configured_backup_dir()).expanduser().resolve()
    if not root.exists():
        return {
            "retained_count": 0,
            "deleted_count": 0,
            "deleted_files": [],
            "retained_files": [],
        }

    backups = sorted(
        (p for p in root.glob("*.db") if p.is_file()),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )

    keep_count = max(1, int(max_backups))
    retained = backups[:keep_count]
    to_delete = backups[keep_count:]

    deleted_names = []
    for old_backup in to_delete:
        try:
            old_backup.unlink()
            deleted_names.append(old_backup.name)
        except OSError:
            pass

    return {
        "operation": "rotate_backups",
        "max_backups": keep_count,
        "retained_count": len(retained),
        "deleted_count": len(deleted_names),
        "retained_files": [p.name for p in retained],
        "deleted_files": deleted_names,
        "timestamp": datetime.now().astimezone().isoformat(),
    }


def prune_historical_raw_events(
    retention_days: int = 90,
    dry_run: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    修剪超過 retention_days 天的高頻原始活動記錄（如 file_activity_events 與 window_events）。
    保留每日摘要（DailySummary）與檢查點（IngestionCheckpoint），維持資料庫精簡流暢。
    """
    path = Path(db_path or configured_database_path()).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=max(1, int(retention_days)))
    cutoff_iso = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    deleted_counts: dict[str, int] = {}
    target_tables = [
        ("file_activity_events", "timestamp"),
        ("window_events", "start_time"),
    ]

    with closing(sqlite3.connect(path, timeout=30)) as conn:
        cursor = conn.cursor()
        for table, time_col in target_tables:
            # 檢查表格是否存在
            check = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()
            if not check:
                continue

            # 檢查欄位是否存在，若不存在則動態檢測時間欄位
            col_info = cursor.execute(f"PRAGMA table_info({table})").fetchall()
            col_names = [col[1] for col in col_info]
            active_time_col = time_col if time_col in col_names else ("timestamp" if "timestamp" in col_names else None)
            if not active_time_col:
                continue

            count_query = f"SELECT COUNT(*) FROM {table} WHERE {active_time_col} < ?"
            count = cursor.execute(count_query, (cutoff_iso,)).fetchone()[0]
            deleted_counts[table] = count

            if not dry_run and count > 0:
                delete_query = f"DELETE FROM {table} WHERE {active_time_col} < ?"
                cursor.execute(delete_query, (cutoff_iso,))
        
        if not dry_run:
            conn.commit()

    return {
        "operation": "prune_historical_raw_events",
        "retention_days": retention_days,
        "cutoff_timestamp": cutoff_iso,
        "dry_run": dry_run,
        "deleted_records": deleted_counts,
        "total_pruned": sum(deleted_counts.values()),
        "timestamp": datetime.now().astimezone().isoformat(),
    }


def run_database_maintenance(
    db_path: str | Path | None = None,
    max_backups: int = 7,
    retention_days: int = 90,
    do_backup: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    綜合執行 SQLite 完整健康維護：
    1. 執行 WAL Checkpoint (TRUNCATE)
    2. 檢查資料庫完整性 (PRAGMA integrity_check)
    3. 執行歷史原始事件修剪
    4. 建立線上 Verified Backup（可選）
    5. 執行備份滾動輪替 (保留最新 N 份)
    6. 再次 WAL Checkpoint 確保乾淨狀態
    7. 輸出持久化 maintenance_receipt.json
    """
    path = Path(db_path or configured_database_path()).expanduser().resolve()
    
    # 1. 首次 Checkpoint
    chk_before = checkpoint_sqlite_database(path, mode="TRUNCATE") if not dry_run else {"dry_run": True}

    # 2. 完整性檢查
    verify_res = verify_sqlite_database(path)

    # 3. 歷史事件修剪
    prune_res = prune_historical_raw_events(retention_days=retention_days, db_path=path, dry_run=dry_run)

    # 4. 備份
    backup_receipt = None
    if do_backup and not dry_run:
        backup_receipt = create_configured_backup()

    # 5. 備份輪替
    rotation_res = rotate_backups(max_backups=max_backups) if not dry_run else {"dry_run": True}

    # 6. 二次 Checkpoint
    chk_after = checkpoint_sqlite_database(path, mode="TRUNCATE") if not dry_run else {"dry_run": True}

    receipt = {
        "receipt_version": 1,
        "operation": "database_maintenance",
        "status": "passed" if verify_res["integrity"] == "ok" else "warning",
        "database": str(path),
        "integrity": verify_res["integrity"],
        "size_bytes": verify_res["size_bytes"],
        "checkpoint_initial": chk_before,
        "checkpoint_final": chk_after,
        "pruning": prune_res,
        "backup": backup_receipt,
        "backup_rotation": rotation_res,
        "created_at": datetime.now().astimezone().isoformat(),
    }

    # 持久化維護收據
    backup_dir = Path(configured_backup_dir()).expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    receipt_file = backup_dir / "latest_maintenance_receipt.json"
    try:
        temp_file = receipt_file.with_suffix(".json.tmp")
        temp_file.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )
        temp_file.replace(receipt_file)
        receipt["receipt_path"] = str(receipt_file)
    except Exception:
        pass

    return receipt


def get_latest_maintenance_receipt() -> dict[str, Any] | None:
    """取得最近一次維護的收據檔案。"""
    backup_dir = Path(configured_backup_dir()).expanduser().resolve()
    receipt_file = backup_dir / "latest_maintenance_receipt.json"
    if not receipt_file.is_file():
        return None
    try:
        return json.loads(receipt_file.read_text(encoding="utf-8"))
    except Exception:
        return None
