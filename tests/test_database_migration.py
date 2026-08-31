import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine, inspect, text

from core.migrations import (
    MIGRATIONS,
    MigrationDefinition,
    MigrationError,
    inspect_migration_status,
    upgrade_sqlite_database,
)


def test_fresh_database_reaches_latest_version_and_rerun_is_idempotent(tmp_path):
    database_path = tmp_path / "fresh.db"
    backup_dir = tmp_path / "backups"

    first = upgrade_sqlite_database(
        database_path,
        backup_before=True,
        backup_dir=backup_dir,
    )
    assert first["before"]["state"] == "fresh"
    assert first["applied_now"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    assert first["pre_migration_backup"] is None
    assert first["after"]["state"] == "up_to_date"

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert "schema_migrations" in inspector.get_table_names()
    assert "ai_prompt_events" in inspector.get_table_names()
    assert "milestone_notification_receipts" in inspector.get_table_names()
    assert "browser_extension_heartbeats" in inspector.get_table_names()
    assert "semantic_documents" in inspector.get_table_names()
    assert "rag_indexed_folders" in inspector.get_table_names()
    assert "rag_indexed_files" in inspector.get_table_names()
    assert "rag_chat_sessions" in inspector.get_table_names()
    assert "rag_chat_messages" in inspector.get_table_names()
    assert "rag_index_jobs" in inspector.get_table_names()
    assert "background_task_runs" in inspector.get_table_names()
    assert "coverage_ledger_intervals" in inspector.get_table_names()
    assert "agent_execution_receipts" in inspector.get_table_names()
    assert "activity_micro_summaries" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("ai_prompt_events")}
    assert "ux_ai_prompt_events_turn_key" in indexes
    engine.dispose()

    second = upgrade_sqlite_database(
        database_path,
        backup_before=True,
        backup_dir=backup_dir,
    )
    assert second["applied_now"] == []
    assert second["pre_migration_backup"] is None
    assert second["after"]["applied_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]


def test_legacy_database_is_backed_up_upgraded_and_data_is_preserved(tmp_path):
    database_path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE ai_prompt_events (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME,
                platform VARCHAR(50) NOT NULL,
                url VARCHAR(500),
                conversation_id VARCHAR(100),
                prompt_text TEXT NOT NULL,
                response_text TEXT,
                project_tag VARCHAR(255),
                metadata_json TEXT
            );
            CREATE TABLE open_loops (
                id INTEGER PRIMARY KEY,
                project_key VARCHAR(255) NOT NULL,
                title TEXT NOT NULL,
                source_type VARCHAR(50),
                source_event_id INTEGER,
                confidence FLOAT,
                created_at DATETIME,
                resolved_at DATETIME
            );
            INSERT INTO ai_prompt_events
                (id, timestamp, platform, prompt_text, response_text)
            VALUES (1, '2026-08-20 10:00:00', 'codex', 'keep prompt', 'keep response');
            INSERT INTO open_loops
                (id, project_key, title, created_at, resolved_at)
            VALUES
                (1, 'alpha', 'still open', '2026-08-20 10:00:00', NULL),
                (2, 'alpha', 'already done', '2026-08-20 11:00:00', '2026-08-21 12:00:00');
            """
        )
        connection.commit()

    receipt = upgrade_sqlite_database(
        database_path,
        backup_before=True,
        backup_dir=tmp_path / "backups",
    )
    assert receipt["before"]["state"] == "unversioned"
    assert receipt["pre_migration_backup"]["integrity"] == "ok"
    assert receipt["applied_now"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

    with closing(sqlite3.connect(database_path)) as connection:
        ai_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ai_prompt_events)")
        }
        assert {
            "cwd",
            "turn_key",
            "source_path",
            "source_position",
            "response_status",
        }.issubset(ai_columns)
        preserved = connection.execute(
            "SELECT prompt_text, response_text FROM ai_prompt_events WHERE id=1"
        ).fetchone()
        assert preserved == ("keep prompt", "keep response")
        lifecycle = connection.execute(
            "SELECT id, status, last_seen_at, updated_at FROM open_loops ORDER BY id"
        ).fetchall()
        assert lifecycle[0][1] == "open"
        assert lifecycle[1][1] == "resolved"
        assert all(row[2] and row[3] for row in lifecycle)
        history_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        assert history_count == 15


def test_checksum_mismatch_is_incompatible_and_fails_closed(tmp_path):
    database_path = tmp_path / "checksum.db"
    upgrade_sqlite_database(database_path, backup_before=False)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum='tampered' WHERE version=1"
        )
        connection.commit()

    status = inspect_migration_status(database_path)
    assert status["state"] == "incompatible"
    assert "checksum mismatch" in status["error"].lower()
    with pytest.raises(MigrationError, match="checksum mismatch"):
        upgrade_sqlite_database(database_path, backup_before=False)


def test_unknown_newer_version_is_rejected(tmp_path):
    database_path = tmp_path / "newer.db"
    upgrade_sqlite_database(database_path, backup_before=False)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "INSERT INTO schema_migrations "
            "(version, name, checksum, applied_at, duration_ms) "
                "VALUES (16, 'future', 'future-checksum', '2026-08-24T00:00:00+08:00', 0)"
        )
        connection.commit()

    status = inspect_migration_status(database_path)
    assert status["state"] == "incompatible"
    assert "newer than this runtime" in status["error"]
    with pytest.raises(MigrationError, match="newer than this runtime"):
        upgrade_sqlite_database(database_path, backup_before=False)


def test_failed_migration_does_not_write_applied_receipt(tmp_path):
    database_path = tmp_path / "failed.db"

    def fail_after_ddl(connection):
        connection.execute(text("CREATE TABLE doomed_table (id INTEGER PRIMARY KEY)"))
        raise ValueError("intentional migration failure")

    definitions = (
        MigrationDefinition(1, "intentional_failure", "v1", fail_after_ddl),
    )
    with pytest.raises(MigrationError, match="intentional migration failure"):
        upgrade_sqlite_database(
            database_path,
            backup_before=False,
            definitions=definitions,
        )

    with closing(sqlite3.connect(database_path)) as connection:
        applied = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
    assert applied == 0
    status = inspect_migration_status(database_path, definitions)
    assert status["state"] == "unversioned"
    assert status["pending_versions"] == [1]


def test_registry_is_contiguous_and_checksums_are_stable():
    assert [migration.version for migration in MIGRATIONS] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    assert all(len(migration.checksum) == 64 for migration in MIGRATIONS)


def test_versioned_database_does_not_bypass_registry_with_create_all(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "versioned.db"
    upgrade_sqlite_database(database_path, backup_before=False)

    def migration_016(connection):
        connection.execute(text(
            "CREATE TABLE registry_only_table (id INTEGER PRIMARY KEY)"
        ))

    definitions = MIGRATIONS + (
        MigrationDefinition(
            16,
            "registry_only_schema_change",
            "create:registry_only_table",
            migration_016,
        ),
    )

    def reject_create_all(*args, **kwargs):
        raise AssertionError("create_all must not run on a versioned database")

    monkeypatch.setattr("core.migrations.Base.metadata.create_all", reject_create_all)
    receipt = upgrade_sqlite_database(
        database_path,
        backup_before=False,
        definitions=definitions,
    )

    assert receipt["applied_now"] == [16]
    assert receipt["after"]["state"] == "up_to_date"
