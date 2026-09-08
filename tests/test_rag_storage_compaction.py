"""Chroma 目錄的空間回收契約（ADR-009 Addendum C）。

Chroma 的 `delete_collection` 只做邏輯刪除：實測（2026-09-07，chromadb 1.5.9）刪掉
一個 4,000 切片的 collection 之後，目錄大小 14,209,408 bytes 一個位元組都沒少——
chroma.sqlite3 的 1,791 頁裡有 1,590 頁變成空頁，舊的 HNSW 片段目錄整個留著且不再
被 segments 表引用。使用者實機因此出現「索引 4,839 切片、chroma 目錄 4.24 GB」。

這裡鎖住回收的三條 fail-closed 規則：
1. 讀不到 segments 表就什麼都不刪（分不出孤兒與活片段時，寧可不動）。
2. 只刪名稱是 UUID 且不在 segments 表裡的目錄；活片段、認不得的東西、
   chroma.sqlite3 本身永遠不碰。
3. confirm=False 一律拒絕；刪不掉的如實記在 failed_dirs。

測試自己組一個假的 Chroma 目錄，不 import chromadb，也不下載任何模型。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rag import storage as storage_module

LIVE = "b5e1cd3f-8393-42a3-bbbf-19af08158241"
ORPHAN = "eb2894eb-7e2b-416e-95ae-833c5cbff543"


def _write_dir(root: Path, name: str, payload: bytes) -> Path:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "data_level0.bin").write_bytes(payload)
    return target


@pytest.fixture
def chroma_dir(tmp_path, monkeypatch):
    """一個假的 Chroma 目錄：一個活片段、一個孤兒、一個認不得的東西。"""
    root = tmp_path / "chroma"
    root.mkdir()
    db = root / "chroma.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE segments (id TEXT PRIMARY KEY, collection TEXT)")
        connection.execute("INSERT INTO segments VALUES (?, ?)", (LIVE, "coll-1"))
        connection.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, blob BLOB)")
        connection.executemany(
            "INSERT INTO embeddings (blob) VALUES (?)", [(b"x" * 4096,) for _ in range(400)]
        )
    _write_dir(root, LIVE, b"L" * 2048)
    _write_dir(root, ORPHAN, b"O" * 8192)
    (root / "notes.txt").write_bytes(b"human file")

    monkeypatch.setattr(type(storage_module.rag_settings), "CHROMA_DIR",
                        property(lambda self: root))
    return root


# ---- 1. 帳要算得出「哪些是刪掉沒回收的」 ----


def test_report_separates_live_segments_from_orphans(chroma_dir):
    report = storage_module.chroma_report()
    assert report["segments_readable"] is True and report["reason"] is None
    assert [item["name"] for item in report["live_segment_dirs"]] == [LIVE]
    assert [item["name"] for item in report["orphan_dirs"]] == [ORPHAN]
    # 不是 UUID 目錄的東西只回報，不歸類成孤兒
    assert [item["name"] for item in report["unknown_entries"]] == ["notes.txt"]
    assert report["orphan_bytes"] == report["orphan_dirs"][0]["bytes"] > 0
    assert report["reclaimable_bytes"] >= report["orphan_bytes"]
    assert report["total_bytes"] > report["sqlite_bytes"]


def test_report_refuses_to_guess_when_segments_are_unreadable(chroma_dir):
    (chroma_dir / "chroma.sqlite3").write_bytes(b"not a database")
    report = storage_module.chroma_report()
    assert report["segments_readable"] is False and report["reason"]
    # 分不出活的與孤兒時，兩個目錄都只能是「不知道」——不是孤兒
    assert report["orphan_dirs"] == [] and report["orphan_bytes"] == 0
    assert {item["name"] for item in report["unknown_entries"]} == {LIVE, ORPHAN, "notes.txt"}


# ---- 2. 回收只碰孤兒 ----


def test_compact_removes_only_orphan_dirs_and_vacuums(chroma_dir):
    sqlite_before = (chroma_dir / "chroma.sqlite3").stat().st_size
    with sqlite3.connect(chroma_dir / "chroma.sqlite3") as connection:
        connection.execute("DELETE FROM embeddings")   # 製造空頁，VACUUM 才有東西可收

    result = storage_module.compact_chroma(confirm=True)

    assert result["operation"] == "compact_chroma"
    assert [item["name"] for item in result["removed_dirs"]] == [ORPHAN]
    assert result["failed_dirs"] == []
    assert not (chroma_dir / ORPHAN).exists()
    # 活片段、認不得的檔案、資料庫本身都還在
    assert (chroma_dir / LIVE / "data_level0.bin").read_bytes() == b"L" * 2048
    assert (chroma_dir / "notes.txt").exists() and (chroma_dir / "chroma.sqlite3").is_file()
    assert result["vacuum"]["ran"] is True and result["vacuum"]["integrity"] == "ok"
    assert (chroma_dir / "chroma.sqlite3").stat().st_size < sqlite_before
    assert result["reclaimed_bytes"] > 0 and result["after_bytes"] < result["before_bytes"]
    assert result["after"]["orphan_dirs"] == []


def test_compact_needs_confirmation(chroma_dir):
    with pytest.raises(ValueError):
        storage_module.compact_chroma()
    assert (chroma_dir / ORPHAN).is_dir()


def test_compact_deletes_nothing_when_it_cannot_tell_orphans_apart(chroma_dir):
    (chroma_dir / "chroma.sqlite3").write_bytes(b"not a database")
    with pytest.raises(RuntimeError) as excinfo:
        storage_module.compact_chroma(confirm=True)
    assert "segments" in str(excinfo.value)
    assert (chroma_dir / ORPHAN).is_dir() and (chroma_dir / LIVE).is_dir()


def test_a_dir_that_became_live_again_is_left_alone(chroma_dir, monkeypatch):
    """孤兒清單是先算出來的；真正刪之前會重讀 segments 表，其間變成活的就跳過。"""
    real_report = storage_module.chroma_report

    def _report_then_adopt_the_orphan():
        report = real_report()
        with sqlite3.connect(chroma_dir / "chroma.sqlite3") as connection:
            connection.execute("INSERT OR IGNORE INTO segments VALUES (?, ?)", (ORPHAN, "coll-2"))
        return report

    monkeypatch.setattr(storage_module, "chroma_report", _report_then_adopt_the_orphan)
    result = storage_module.compact_chroma(confirm=True)
    assert result["removed_dirs"] == [] and (chroma_dir / ORPHAN).is_dir()


# ---- 3. 端點：需要 confirm，且先請檢索 worker 讓開 ----


def test_endpoint_requires_confirmation_and_releases_the_retrieval_worker(monkeypatch):
    from fastapi.testclient import TestClient

    import rag.router as router_module
    from core.server import app

    client = TestClient(app)
    origin = {"Origin": "http://127.0.0.1:8765"}
    started: list[str] = []
    released: list[bool] = []
    monkeypatch.setattr(router_module, "_start_job_or_raise",
                        lambda job_type, *a, **k: started.append(job_type) or {"id": "job-1"})
    monkeypatch.setattr(router_module.retrieval_client, "shutdown",
                        lambda: released.append(True) or {"state": "cold"})

    res = client.post("/api/v1/rag/storage/compact-chroma", json={}, headers=origin)
    assert res.status_code == 400 and started == [] and released == []

    res = client.post("/api/v1/rag/storage/compact-chroma", json={"confirm": True}, headers=origin)
    assert res.status_code == 200
    assert started == ["compact_chroma"] and released == [True]
    assert res.json()["retrieval_worker"] == "cold"


def test_compact_chroma_is_a_known_job_type():
    from rag.jobs import create_job

    with pytest.raises(ValueError):
        create_job("compact_everything")
