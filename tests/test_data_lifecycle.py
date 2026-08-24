import sqlite3

from core.data_lifecycle import backup_sqlite_database, verify_sqlite_database


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
