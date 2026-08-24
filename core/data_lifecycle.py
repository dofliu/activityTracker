"""SQLite data lifecycle operations with verifiable, non-destructive backups."""

from __future__ import annotations

import hashlib
import sqlite3
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
    with sqlite3.connect(path) as connection:
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

    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
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
