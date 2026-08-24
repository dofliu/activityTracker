import json
import os
import sqlite3
from pathlib import Path

from core.data_lifecycle import (
    backup_sqlite_database,
    latest_backup_path,
    restore_drill,
    verify_sqlite_database,
)


def test_sqlite_online_backup_is_integrity_checked(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence(value) VALUES ('preserved')")
        connection.commit()

    receipt = backup_sqlite_database(source, tmp_path / "backups")
    assert receipt["integrity"] == "ok"
    assert "evidence" in receipt["tables"]
    assert len(receipt["sha256"]) == 64

    verified = verify_sqlite_database(receipt["path"])
    with sqlite3.connect(verified["path"]) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"


def test_restore_drill_preserves_schema_and_row_counts_without_live_overwrite(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO evidence(value) VALUES (?)",
            [("first",), ("second",)],
        )
        connection.execute("CREATE INDEX ix_evidence_value ON evidence(value)")
        connection.commit()

    backup = backup_sqlite_database(source, tmp_path / "backups")
    receipt = restore_drill(backup["path"], tmp_path / "receipts")

    assert receipt["status"] == "passed"
    assert all(receipt["checks"].values())
    assert receipt["source_backup"]["row_counts"] == {"evidence": 2}
    assert receipt["isolated_restore"]["temporary_copy_retained"] is False
    assert source.exists()
    assert Path(receipt["receipt_path"]).is_file()
    assert not list(tmp_path.rglob("restored.db"))

    persisted = json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "passed"
    assert persisted["checks"]["schema_match"] is True


def test_latest_backup_path_selects_most_recent_file(tmp_path):
    older = tmp_path / "older.db"
    newer = tmp_path / "newer.db"
    older.write_bytes(b"older")
    newer.write_bytes(b"newer")
    older_stat = older.stat()
    # 以明確的 nanosecond 時間避免依賴檔名或檔案系統解析度。
    os.utime(older, ns=(older_stat.st_atime_ns, 1_000_000_000))
    os.utime(newer, ns=(older_stat.st_atime_ns, 2_000_000_000))
    assert latest_backup_path(tmp_path) == newer.resolve()
