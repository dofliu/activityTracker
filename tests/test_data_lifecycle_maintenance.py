import os
import sqlite3
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from core.data_lifecycle import (
    checkpoint_sqlite_database,
    rotate_backups,
    prune_historical_raw_events,
    run_database_maintenance,
    get_latest_maintenance_receipt,
    verify_sqlite_database,
)
from rag.scanner import FileScanner
from rag.parsers.parser_hub import parser_hub


import gc

@pytest.fixture
def temp_wal_db():
    tmpdir = tempfile.TemporaryDirectory(prefix="omnicontext-test-db-", ignore_cleanup_errors=True)
    db_path = Path(tmpdir.name) / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE file_activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                file_path TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE window_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                app_name TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_str TEXT NOT NULL,
                summary_text TEXT NOT NULL
            );
            """
        )
        # 寫入歷史舊資料與近期資料
        old_time = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("INSERT INTO file_activity_events (timestamp, file_path) VALUES (?, ?)", (old_time, "/old/file1.py"))
        conn.execute("INSERT INTO file_activity_events (timestamp, file_path) VALUES (?, ?)", (old_time, "/old/file2.py"))
        conn.execute("INSERT INTO file_activity_events (timestamp, file_path) VALUES (?, ?)", (now_time, "/recent/file.py"))

        conn.execute("INSERT INTO window_events (timestamp, app_name) VALUES (?, ?)", (old_time, "OldApp"))
        conn.execute("INSERT INTO window_events (timestamp, app_name) VALUES (?, ?)", (now_time, "CurrentApp"))

        conn.execute("INSERT INTO daily_summaries (date_str, summary_text) VALUES (?, ?)", ("2020-01-01", "Preserved summary"))
        conn.commit()
    finally:
        conn.close()

    try:
        yield db_path
    finally:
        gc.collect()
        try:
            tmpdir.cleanup()
        except Exception:
            pass


def test_checkpoint_sqlite_database(temp_wal_db):
    receipt = checkpoint_sqlite_database(temp_wal_db, mode="TRUNCATE")
    assert receipt["operation"] == "wal_checkpoint"
    assert receipt["mode"] == "TRUNCATE"
    assert receipt["busy"] is False
    assert "log_pages" in receipt
    assert "checkpointed_pages" in receipt
    assert receipt["database"] == str(temp_wal_db.resolve())


def test_rotate_backups():
    with tempfile.TemporaryDirectory(prefix="omnicontext-test-backups-") as tmpdir:
        backup_dir = Path(tmpdir)
        # 建立 8 個模擬備份檔案
        for i in range(8):
            file = backup_dir / f"omni_context-2026010{i+1}-120000.db"
            file.write_text("dummy backup", encoding="utf-8")
            # 設定不同修改時間
            mtime = time.time() - (8 - i) * 100
            os.utime(file, (mtime, mtime))

        # 執行輪替，保留最新 3 份
        receipt = rotate_backups(backup_dir=backup_dir, max_backups=3)
        assert receipt["operation"] == "rotate_backups"
        assert receipt["max_backups"] == 3
        assert receipt["retained_count"] == 3
        assert receipt["deleted_count"] == 5

        remaining = list(backup_dir.glob("*.db"))
        assert len(remaining) == 3


def test_prune_historical_raw_events(temp_wal_db):
    # 測試修剪 90 天前的舊記錄
    prune_res = prune_historical_raw_events(retention_days=90, db_path=temp_wal_db)
    assert prune_res["operation"] == "prune_historical_raw_events"
    assert prune_res["retention_days"] == 90
    assert prune_res["deleted_records"]["file_activity_events"] == 2
    assert prune_res["deleted_records"]["window_events"] == 1
    assert prune_res["total_pruned"] == 3

    # 驗證資料庫現狀：舊事件已刪除，新事件與 summary 依然存在
    with sqlite3.connect(temp_wal_db) as conn:
        cursor = conn.cursor()
        file_count = cursor.execute("SELECT COUNT(*) FROM file_activity_events").fetchone()[0]
        window_count = cursor.execute("SELECT COUNT(*) FROM window_events").fetchone()[0]
        summary_count = cursor.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0]

        assert file_count == 1  # 僅剩 recent
        assert window_count == 1  # 僅剩 recent
        assert summary_count == 1  # summary 未被更動


def test_run_database_maintenance(temp_wal_db):
    receipt = run_database_maintenance(
        db_path=temp_wal_db,
        max_backups=3,
        retention_days=90,
        do_backup=False
    )
    assert receipt["operation"] == "database_maintenance"
    assert receipt["status"] == "passed"
    assert receipt["integrity"] == "ok"
    assert "checkpoint_initial" in receipt
    assert "checkpoint_final" in receipt
    assert "pruning" in receipt


def test_rag_temporary_file_filtering():
    scanner = FileScanner()

    # 暫存與鎖定檔名應被忽略
    assert scanner._should_index_file("~$MyResearch.docx") is False
    assert scanner._should_index_file("~$Financial.xlsx") is False
    assert scanner._should_index_file(".~lock.Paper.docx#") is False
    assert scanner._should_index_file(".hidden_file.py") is False
    assert scanner._should_index_file("downloading.crdownload") is False
    assert scanner._should_index_file("tempfile.tmp") is False
    assert scanner._should_index_file("vim.swp") is False

    # 正常文件與代碼應被接受
    assert scanner._should_index_file("MyResearch.docx") is True
    assert scanner._should_index_file("Financial.xlsx") is True
    assert scanner._should_index_file("Presentation.pptx") is True
    assert scanner._should_index_file("Paper.pdf") is True
    assert scanner._should_index_file("main.py") is True
    assert scanner._should_index_file("README.md") is True

    # parser_hub.parse_file 對暫存檔的安全防呆
    parsed = parser_hub.parse_file("C:/Projects/~$Secret.docx")
    assert len(parsed.sections) == 0
    assert parsed.metadata.get("skipped") == "temporary_or_locked_file"
