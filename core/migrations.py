"""Append-only SQLite schema migrations with checksum and backup guards."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from core.data_lifecycle import backup_sqlite_database, configured_backup_dir
from core.models import Base


class MigrationError(RuntimeError):
    """Migration contract 不相容或執行失敗時，阻止應用程式繼續啟動。"""


@dataclass(frozen=True)
class MigrationDefinition:
    version: int
    name: str
    signature: str
    apply: Callable[[Connection], None]

    @property
    def checksum(self) -> str:
        payload = f"{self.version}:{self.name}:{self.signature}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _table_exists(connection: Connection, table_name: str) -> bool:
    return connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table_name},
    ).first() is not None


def _add_columns_if_missing(
    connection: Connection,
    table_name: str,
    columns: dict[str, str],
) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(text(f"PRAGMA table_info({table_name});")).fetchall()
    existing = {row[1] for row in rows}
    added = set()
    for column_name, declaration in columns.items():
        if column_name not in existing:
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_name} {declaration};"
                )
            )
            added.add(column_name)
    return added


def _migration_001_ai_provenance(connection: Connection) -> None:
    _add_columns_if_missing(
        connection,
        "ai_prompt_events",
        {
            "cwd": "TEXT",
            "turn_key": "VARCHAR(128)",
            "source_path": "VARCHAR(1500)",
            "source_position": "INTEGER",
            "response_status": "VARCHAR(50)",
        },
    )
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_prompt_events_turn_key "
        "ON ai_prompt_events(turn_key) WHERE turn_key IS NOT NULL;"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ai_prompt_events_response_status "
        "ON ai_prompt_events(response_status);"
    ))


def _migration_002_open_loop_lifecycle(connection: Connection) -> None:
    added = _add_columns_if_missing(
        connection,
        "open_loops",
        {
            "status": "VARCHAR(30) NOT NULL DEFAULT 'open'",
            "fingerprint": "VARCHAR(64)",
            "last_seen_at": "DATETIME",
            "updated_at": "DATETIME",
            "resolution_note": "TEXT",
        },
    )
    if _table_exists(connection, "open_loops"):
        if "status" in added:
            connection.execute(text(
                "UPDATE open_loops SET status = CASE "
                "WHEN resolved_at IS NULL THEN 'open' ELSE 'resolved' END"
            ))
        else:
            connection.execute(text(
                "UPDATE open_loops SET status = CASE "
                "WHEN resolved_at IS NULL THEN 'open' ELSE 'resolved' END "
                "WHERE status IS NULL OR status = ''"
            ))
        connection.execute(text(
            "UPDATE open_loops "
            "SET last_seen_at = COALESCE(last_seen_at, created_at), "
            "updated_at = COALESCE(updated_at, created_at)"
        ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_open_loops_status ON open_loops(status);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_open_loops_fingerprint "
        "ON open_loops(fingerprint);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_open_loops_last_seen_at "
        "ON open_loops(last_seen_at);"
    ))


def _migration_003_ingestion_checkpoints(connection: Connection) -> None:
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ingestion_checkpoints_source_path "
        "ON ingestion_checkpoints(source_path);"
    ))


def _migration_004_milestone_receipts(connection: Connection) -> None:
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_milestone_receipt_date_threshold_channel "
        "ON milestone_notification_receipts(local_date, milestone_minutes, channel);"
    ))


MIGRATIONS: tuple[MigrationDefinition, ...] = (
    MigrationDefinition(
        1,
        "ai_provenance_and_turn_identity",
        "ai_prompt_events:add:cwd,turn_key,source_path,source_position,response_status;"
        "indexes:ux_turn_key,ix_response_status",
        _migration_001_ai_provenance,
    ),
    MigrationDefinition(
        2,
        "open_loop_lifecycle",
        "open_loops:add:status,fingerprint,last_seen_at,updated_at,resolution_note;"
        "backfill:status,last_seen_at,updated_at;indexes:status,fingerprint,last_seen_at",
        _migration_002_open_loop_lifecycle,
    ),
    MigrationDefinition(
        3,
        "ingestion_checkpoint_identity",
        "ingestion_checkpoints:index:unique_source_path",
        _migration_003_ingestion_checkpoints,
    ),
    MigrationDefinition(
        4,
        "usage_milestone_receipt_identity",
        "milestone_notification_receipts:index:unique_date_threshold_channel",
        _migration_004_milestone_receipts,
    ),
)


def _validate_registry(definitions: Sequence[MigrationDefinition]) -> None:
    versions = [item.version for item in definitions]
    names = [item.name for item in definitions]
    if not versions or versions != sorted(versions):
        raise MigrationError("Migration registry must be non-empty and version-sorted")
    if versions != list(range(1, max(versions) + 1)):
        raise MigrationError("Migration versions must be contiguous and start at 1")
    if len(set(versions)) != len(versions) or len(set(names)) != len(names):
        raise MigrationError("Migration versions and names must be unique")


def _ensure_history_table(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0)
            );
            """
        ))


def _read_applied(engine: Engine) -> list[dict]:
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT version, name, checksum, applied_at, duration_ms "
            "FROM schema_migrations ORDER BY version"
        )).mappings().all()
    return [dict(row) for row in rows]


def _validate_applied(
    applied: Sequence[dict],
    definitions: Sequence[MigrationDefinition],
) -> None:
    registered = {item.version: item for item in definitions}
    applied_versions = [int(row["version"]) for row in applied]
    unknown = [version for version in applied_versions if version not in registered]
    if unknown:
        raise MigrationError(
            "Database contains migration versions newer than this runtime: "
            + ", ".join(map(str, unknown))
        )
    if applied_versions and applied_versions != list(range(1, max(applied_versions) + 1)):
        raise MigrationError("Database migration history contains a version gap")
    for row in applied:
        definition = registered[int(row["version"])]
        if row["name"] != definition.name or row["checksum"] != definition.checksum:
            raise MigrationError(
                f"Migration checksum mismatch at version {definition.version}; "
                "existing migrations are append-only"
            )


def run_migrations(
    engine: Engine,
    definitions: Sequence[MigrationDefinition] = MIGRATIONS,
) -> dict:
    """Apply pending migrations one transaction at a time and return a receipt."""
    _validate_registry(definitions)
    _ensure_history_table(engine)
    applied_before = _read_applied(engine)
    _validate_applied(applied_before, definitions)
    applied_versions = {int(row["version"]) for row in applied_before}
    applied_now = []

    for definition in definitions:
        if definition.version in applied_versions:
            continue
        started = time.perf_counter()
        try:
            with engine.begin() as connection:
                definition.apply(connection)
                duration_ms = max(0, round((time.perf_counter() - started) * 1000))
                applied_at = datetime.now().astimezone().isoformat()
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations "
                        "(version, name, checksum, applied_at, duration_ms) "
                        "VALUES (:version, :name, :checksum, :applied_at, :duration_ms)"
                    ),
                    {
                        "version": definition.version,
                        "name": definition.name,
                        "checksum": definition.checksum,
                        "applied_at": applied_at,
                        "duration_ms": duration_ms,
                    },
                )
            applied_now.append(definition.version)
        except Exception as exc:
            raise MigrationError(
                f"Migration {definition.version} ({definition.name}) failed: {exc}"
            ) from exc

    applied_after = _read_applied(engine)
    _validate_applied(applied_after, definitions)
    return {
        "status": "up_to_date",
        "current_version": max((row["version"] for row in applied_after), default=0),
        "latest_version": definitions[-1].version,
        "applied_now": applied_now,
        "applied": applied_after,
        "pending_versions": [],
    }


def _read_history_from_sqlite(path: Path) -> tuple[list[str], list[dict]]:
    uri = f"{path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        if "schema_migrations" not in tables:
            return tables, []
        rows = connection.execute(
            "SELECT version, name, checksum, applied_at, duration_ms "
            "FROM schema_migrations ORDER BY version"
        ).fetchall()
    keys = ("version", "name", "checksum", "applied_at", "duration_ms")
    return tables, [dict(zip(keys, row)) for row in rows]


def inspect_migration_status(
    db_path: str | Path,
    definitions: Sequence[MigrationDefinition] = MIGRATIONS,
) -> dict:
    """Read-only migration status; does not create or alter the database."""
    _validate_registry(definitions)
    path = Path(db_path).expanduser().resolve()
    latest = definitions[-1].version
    if not path.is_file():
        return {
            "database_path": str(path),
            "database_exists": False,
            "has_user_tables": False,
            "state": "fresh",
            "current_version": 0,
            "latest_version": latest,
            "applied_versions": [],
            "pending_versions": [item.version for item in definitions],
            "error": None,
        }

    tables, applied = _read_history_from_sqlite(path)
    current = max((int(row["version"]) for row in applied), default=0)
    error = None
    try:
        _validate_applied(applied, definitions)
    except MigrationError as exc:
        error = str(exc)
    applied_versions = [int(row["version"]) for row in applied]
    pending = [item.version for item in definitions if item.version not in applied_versions]
    if error:
        state = "incompatible"
    elif not applied:
        state = "unversioned" if tables else "fresh"
    elif pending:
        state = "pending"
    else:
        state = "up_to_date"
    return {
        "database_path": str(path),
        "database_exists": True,
        "has_user_tables": any(name != "schema_migrations" for name in tables),
        "state": state,
        "current_version": current,
        "latest_version": latest,
        "applied_versions": applied_versions,
        "pending_versions": pending,
        "error": error,
    }


def upgrade_sqlite_database(
    db_path: str | Path,
    *,
    backup_before: bool = True,
    backup_dir: str | Path | None = None,
    definitions: Sequence[MigrationDefinition] = MIGRATIONS,
) -> dict:
    """Upgrade one SQLite DB and create a verified pre-migration backup when needed."""
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    before = inspect_migration_status(path, definitions)
    if before["state"] == "incompatible":
        raise MigrationError(before["error"] or "Incompatible migration history")

    backup_receipt = None
    if (
        backup_before
        and before["database_exists"]
        and before["has_user_tables"]
        and before["pending_versions"]
    ):
        backup_receipt = backup_sqlite_database(
            path,
            backup_dir or configured_backup_dir(),
        )

    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL;"))
            connection.execute(text("PRAGMA synchronous=NORMAL;"))
        # 只有尚未建立版本歷史的 baseline 可由目前 models 補齊；一旦有
        # version receipt，後續 schema 變更必須完全由 append-only migration 負責。
        if before["state"] in {"fresh", "unversioned"}:
            Base.metadata.create_all(bind=engine)
        migration_receipt = run_migrations(engine, definitions)
    finally:
        engine.dispose()

    after = inspect_migration_status(path, definitions)
    if after["state"] != "up_to_date":
        raise MigrationError(f"Migration finished in unexpected state: {after['state']}")
    return {
        "status": "up_to_date",
        "database_path": str(path),
        "before": before,
        "after": after,
        "applied_now": migration_receipt["applied_now"],
        "pre_migration_backup": backup_receipt,
    }
