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


def configured_database_path() -> Path:
    cfg = get_config()
    configured = cfg.expand_path(cfg.get("database.db_path", "omni_context.db"))
    if configured.is_absolute():
        return configured.resolve()
    return (Path(__file__).parent.parent / configured).resolve()


def configured_backup_dir() -> Path:
    cfg = get_config()
    configured = cfg.expand_path(
        cfg.get("data_lifecycle.backups_dir", "~/OmniContext/backups")
    )
    if configured.is_absolute():
        return configured.resolve()
    return (Path(__file__).parent.parent / configured).resolve()


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
