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


def _migration_005_browser_extension_heartbeat(connection: Connection) -> None:
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS browser_extension_heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id VARCHAR(64) NOT NULL,
            extension_version VARCHAR(32) NOT NULL,
            ready_platforms_json TEXT,
            last_capture_status VARCHAR(40) NOT NULL DEFAULT 'none',
            last_capture_at DATETIME,
            last_error_code VARCHAR(80),
            offline_queue_size INTEGER NOT NULL DEFAULT 0,
            first_seen_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_browser_extension_heartbeat_instance "
        "ON browser_extension_heartbeats(instance_id);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_browser_extension_heartbeat_last_seen "
        "ON browser_extension_heartbeats(last_seen_at);"
    ))


def _migration_006_local_semantic_index(connection: Connection) -> None:
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS semantic_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type VARCHAR(40) NOT NULL,
            source_id VARCHAR(120) NOT NULL,
            source_ref VARCHAR(1500) NOT NULL,
            source_updated_at DATETIME,
            project_key VARCHAR(255),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            trust_status VARCHAR(50) NOT NULL DEFAULT 'observed',
            content_hash VARCHAR(64) NOT NULL,
            embedding_model VARCHAR(120) NOT NULL,
            embedding_dimensions INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            indexed_at DATETIME NOT NULL
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_documents_source "
        "ON semantic_documents(source_type, source_id);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_semantic_documents_source_updated_at "
        "ON semantic_documents(source_updated_at);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_semantic_documents_project_key "
        "ON semantic_documents(project_key);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_semantic_documents_model "
        "ON semantic_documents(embedding_model);"
    ))


def _migration_007_embedding_input_provenance(connection: Connection) -> None:
    _add_columns_if_missing(
        connection,
        "semantic_documents",
        {
            "embedding_input_mode": "VARCHAR(50) NOT NULL DEFAULT 'legacy_unrecorded'",
        },
    )
    if _table_exists(connection, "semantic_documents"):
        connection.execute(text(
            "UPDATE semantic_documents SET embedding_input_mode = "
            "CASE WHEN LENGTH(content) <= 3000 THEN 'legacy_raw_full' "
            "ELSE 'legacy_raw_truncated' END "
            "WHERE embedding_input_mode = 'legacy_unrecorded'"
        ))


def _migration_008_rag_tables(connection: Connection) -> None:
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS rag_indexed_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path VARCHAR(1000) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME,
            last_scanned_at DATETIME,
            file_count INTEGER DEFAULT 0,
            total_size INTEGER DEFAULT 0
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_rag_folders_path "
        "ON rag_indexed_folders(path);"
    ))
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS rag_indexed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER,
            path VARCHAR(1000) UNIQUE NOT NULL,
            filename VARCHAR(255) NOT NULL,
            extension VARCHAR(50) NOT NULL,
            file_size INTEGER DEFAULT 0,
            last_modified REAL DEFAULT 0.0,
            file_hash VARCHAR(64) DEFAULT '',
            chunk_count INTEGER DEFAULT 0,
            status VARCHAR(50) DEFAULT 'pending',
            error_message TEXT,
            indexed_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_rag_files_path "
        "ON rag_indexed_files(path);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rag_files_folder_id "
        "ON rag_indexed_files(folder_id);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rag_files_status "
        "ON rag_indexed_files(status);"
    ))
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS rag_chat_sessions (
            id VARCHAR(100) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        );
        """
    ))
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS rag_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id VARCHAR(100) NOT NULL,
            role VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            citations TEXT,
            provider VARCHAR(50),
            model VARCHAR(100),
            created_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rag_chat_messages_session "
        "ON rag_chat_messages(session_id);"
    ))


def _migration_009_github_issues_and_snoozes(connection: Connection) -> None:
    """GitHub issue 明細與 proposal snooze 回饋。

    repo 層的 open_issues_count 由 GitHub API 提供，但它把 PR 也算進去，
    因此無法回答「有哪些 issue 待處理」。此處改存 issue 明細。
    """
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS github_issue_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_name VARCHAR(100) NOT NULL,
            issue_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            state VARCHAR(20) NOT NULL,
            author VARCHAR(100),
            assignee VARCHAR(100),
            html_url VARCHAR(500) NOT NULL,
            labels_json TEXT,
            comments_count INTEGER DEFAULT 0,
            created_at DATETIME,
            updated_at DATETIME,
            closed_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_github_issue_repo_number "
        "ON github_issue_events(repo_name, issue_number);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_github_issue_state_updated "
        "ON github_issue_events(state, updated_at);"
    ))
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS proposal_snoozes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_type VARCHAR(64) NOT NULL,
            project_key VARCHAR(255) NOT NULL,
            subject_ref VARCHAR(255) NOT NULL DEFAULT '',
            snoozed_until DATETIME,
            dismissed INTEGER DEFAULT 0,
            note TEXT,
            created_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_proposal_snooze_target "
        "ON proposal_snoozes(proposal_type, project_key, subject_ref);"
    ))


def _migration_010_rag_worker_jobs(connection: Connection) -> None:
    """將 DeskRAG 長時間作業改為可恢復、可控制的獨立 worker 工作。"""
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS rag_index_jobs (
            id VARCHAR(36) PRIMARY KEY,
            job_type VARCHAR(32) NOT NULL,
            folder_id INTEGER,
            status VARCHAR(32) NOT NULL DEFAULT 'queued',
            requested_at DATETIME,
            started_at DATETIME,
            completed_at DATETIME,
            updated_at DATETIME,
            worker_pid INTEGER,
            total_files INTEGER DEFAULT 0,
            processed_files INTEGER DEFAULT 0,
            indexed_chunks INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            max_files INTEGER,
            throttle_ms INTEGER DEFAULT 0,
            current_file VARCHAR(1000),
            message TEXT,
            error_message TEXT,
            pause_requested INTEGER DEFAULT 0,
            cancel_requested INTEGER DEFAULT 0
        );
        """
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rag_jobs_status_requested "
        "ON rag_index_jobs(status, requested_at);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_rag_jobs_folder "
        "ON rag_index_jobs(folder_id, requested_at);"
    ))


def _migration_011_rag_worker_result_receipts(connection: Connection) -> None:
    """保留 worker 非內容型結果，讓主服務不必讀取大型 Chroma collection。"""
    columns = {
        row[1] for row in connection.execute(text("PRAGMA table_info(rag_index_jobs)")).fetchall()
    }
    if "result_json" not in columns:
        connection.execute(text("ALTER TABLE rag_index_jobs ADD COLUMN result_json TEXT"))


def _migration_012_background_task_receipts(connection: Connection) -> None:
    """保存本機 agent 任務的成對 start/end receipt，不儲存內容本身。"""
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS background_task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_key VARCHAR(64) UNIQUE NOT NULL,
            platform VARCHAR(50) NOT NULL,
            session_id VARCHAR(100),
            project_tag VARCHAR(255),
            cwd VARCHAR(1000),
            started_at DATETIME NOT NULL,
            completed_at DATETIME,
            duration_seconds FLOAT,
            status VARCHAR(40) NOT NULL DEFAULT 'awaiting_final',
            start_evidence_kind VARCHAR(80) NOT NULL DEFAULT 'user_prompt',
            completion_evidence_kind VARCHAR(80),
            source_path VARCHAR(1500) NOT NULL,
            start_position INTEGER,
            end_position INTEGER,
            observed_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_background_task_runs_task_key "
        "ON background_task_runs(task_key);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_background_tasks_status_started "
        "ON background_task_runs(status, started_at);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_background_tasks_platform_started "
        "ON background_task_runs(platform, started_at);"
    ))


def _migration_013_coverage_ledger(connection: Connection) -> None:
    """P2.6 continuous coverage ledger：記錄採集器實際被觀測運作的時間段。"""
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS coverage_ledger_intervals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collector VARCHAR(64) NOT NULL,
            started_at DATETIME NOT NULL,
            last_heartbeat_at DATETIME NOT NULL,
            heartbeat_count INTEGER NOT NULL DEFAULT 1,
            closed_at DATETIME,
            close_reason VARCHAR(40),
            created_at DATETIME NOT NULL,
            updated_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_coverage_ledger_collector_started "
        "ON coverage_ledger_intervals(collector, started_at);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_coverage_ledger_collector_open "
        "ON coverage_ledger_intervals(collector, closed_at);"
    ))


def _migration_014_agent_execution_receipts(connection: Connection) -> None:
    """ADR-008 gated executor audit receipts。

    刻意使用新表名 agent_execution_receipts：wip/p5-2 遺留的
    agent_execution_jobs（含自由字串 command 欄位）若存在則原樣保留為
    歷史遺留，不遷移、不刪除、不再由 ORM 管理。
    """
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS agent_execution_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id VARCHAR(64) NOT NULL,
            template_id VARCHAR(64) NOT NULL,
            risk_level VARCHAR(20) NOT NULL,
            project_key VARCHAR(255),
            action_call VARCHAR(500) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            approved_via VARCHAR(40) NOT NULL DEFAULT 'web_click',
            requested_at DATETIME NOT NULL,
            started_at DATETIME,
            finished_at DATETIME,
            duration_seconds FLOAT,
            output_digest VARCHAR(64),
            output_summary VARCHAR(500),
            error_code VARCHAR(80),
            created_at DATETIME NOT NULL
        );
        """
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_agent_exec_receipts_status_requested "
        "ON agent_execution_receipts(status, requested_at);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_agent_exec_receipts_proposal_requested "
        "ON agent_execution_receipts(proposal_id, requested_at);"
    ))
    # 一個 proposal 同時間只允許一個 active job（fail-closed dedup）。
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_exec_receipts_active_proposal "
        "ON agent_execution_receipts(proposal_id) "
        "WHERE status IN ('queued', 'running');"
    ))


def _migration_015_activity_micro_summaries(connection: Connection) -> None:
    """兩層增量摘要：checkpoint 時段微摘要（map），日報 reduce 讀取。"""
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS activity_micro_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start DATETIME NOT NULL,
            period_end DATETIME NOT NULL,
            provider VARCHAR(40) NOT NULL,
            model VARCHAR(120),
            summary_text VARCHAR(800) NOT NULL,
            input_chars INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME
        );
        """
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_micro_summary_period "
        "ON activity_micro_summaries(period_start, period_end);"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_activity_micro_summaries_period_start "
        "ON activity_micro_summaries(period_start);"
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
    MigrationDefinition(
        5,
        "browser_extension_verified_heartbeat",
        "browser_extension_heartbeats:create;"
        "indexes:unique_instance,last_seen;privacy:no_url_no_prompt_no_token",
        _migration_005_browser_extension_heartbeat,
    ),
    MigrationDefinition(
        6,
        "local_semantic_index",
        "semantic_documents:create;indexes:unique_source,updated,project,model;"
        "embedding:local_float32_blob;provenance:source_ref_trust_status",
        _migration_006_local_semantic_index,
    ),
    MigrationDefinition(
        7,
        "embedding_input_provenance",
        "semantic_documents:add:embedding_input_mode;"
        "values:normalized_full,normalized_truncated,ascii_fallback,metadata_only,legacy_raw",
        _migration_007_embedding_input_provenance,
    ),
    MigrationDefinition(
        8,
        "rag_knowledge_tables",
        "rag_indexed_folders:create;rag_indexed_files:create;rag_chat_sessions:create;rag_chat_messages:create;"
        "indexes:path,folder_id,status,session_id",
        _migration_008_rag_tables,
    ),
    MigrationDefinition(
        9,
        "github_issues_and_proposal_snoozes",
        "github_issue_events:create;proposal_snoozes:create;"
        "indexes:repo_number,state_updated,snooze_target",
        _migration_009_github_issues_and_snoozes,
    ),
    MigrationDefinition(
        10,
        "rag_worker_job_control",
        "rag_index_jobs:create;indexes:status_requested,folder_requested;"
        "controls:pause,cancel,rate_limit,file_limit",
        _migration_010_rag_worker_jobs,
    ),
    MigrationDefinition(
        11,
        "rag_worker_result_receipts",
        "rag_index_jobs:add:result_json;privacy:no_document_content",
        _migration_011_rag_worker_result_receipts,
    ),
    MigrationDefinition(
        12,
        "background_task_receipts",
        "background_task_runs:create;indexes:unique_key,status_started,platform_started;"
        "privacy:no_prompt_no_response;contract:paired_local_agent_start_end",
        _migration_012_background_task_receipts,
    ),
    MigrationDefinition(
        13,
        "continuous_coverage_ledger",
        "coverage_ledger_intervals:create;indexes:collector_started,collector_open;"
        "claim:observed_collector_runtime_only;end:last_heartbeat_never_extrapolated",
        _migration_013_coverage_ledger,
    ),
    MigrationDefinition(
        14,
        "agent_execution_receipts",
        "agent_execution_receipts:create;indexes:status_requested,proposal_requested,"
        "unique_active_proposal;privacy:no_command_no_content_no_token;"
        "legacy:agent_execution_jobs_left_untouched",
        _migration_014_agent_execution_receipts,
    ),
    MigrationDefinition(
        15,
        "activity_micro_summaries",
        "activity_micro_summaries:create;indexes:unique_period,period_start;"
        "map_reduce:checkpoint_micro_then_daily_reduce;privacy:no_raw_prompt_or_response",
        _migration_015_activity_micro_summaries,
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
