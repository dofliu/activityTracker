import os
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from core.server import app
from core.config import get_config
from core.database import Database


@pytest.fixture
def client(tmp_path):
    test_db = tmp_path / "test_health.db"
    cfg = get_config()
    orig_db = cfg.get("database.db_path")
    cfg._config_data.setdefault("database", {})["db_path"] = str(test_db)
    
    # 初始化資料庫表格與引擎
    db = Database()
    db.init_db()

    with TestClient(app) as test_client:
        yield test_client

    if orig_db is not None:
        cfg._config_data["database"]["db_path"] = orig_db
    else:
        cfg._config_data["database"].pop("db_path", None)


def test_get_system_health(client):
    """驗證 GET /api/v1/system/health 端點回傳完整結構"""
    resp = client.get("/api/v1/system/health")
    assert resp.status_code == 200
    data = resp.json()

    assert "status" in data
    assert "is_running" in data
    assert "watchers" in data
    assert "collector_runtime" in data
    assert "collector_health" in data
    assert "collector_diagnostics" in data
    assert "self_healing" in data
    assert "database" in data
    assert "timestamp" in data

    db_info = data["database"]
    assert "path" in db_info
    assert "size_bytes" in db_info
    assert "wal_size_bytes" in db_info
    assert "active_projects_count" in db_info
    assert db_info["size_bytes"] >= 0


def test_system_health_reads_materialized_project_count_without_refresh(client, monkeypatch):
    """Health 輪詢不可觸發專案重整，否則會把讀取端點變成競爭寫入來源。"""
    def _unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("health endpoint must not refresh project states")

    monkeypatch.setattr("core.project_engine.refresh_project_states", _unexpected_refresh)
    monkeypatch.setattr("core.server.get_project_state_count", lambda: 7)

    response = client.get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json()["database"]["active_projects_count"] == 7


def test_post_system_heal(client):
    """驗證 POST /api/v1/system/heal 觸發巡檢與修復"""
    resp = client.post("/api/v1/system/heal")
    assert resp.status_code == 200
    data = resp.json()

    assert "status" in data
    assert "timestamp" in data
    assert "healed_services" in data
    assert "service_receipts" in data


def test_post_wal_checkpoint(client):
    """驗證 POST /api/v1/system/wal-checkpoint 手動截斷 WAL"""
    resp = client.post("/api/v1/system/wal-checkpoint", json={"mode": "TRUNCATE"})
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("operation") == "wal_checkpoint"
    assert data.get("mode") == "TRUNCATE"
    assert "wal_size_bytes_before" in data
    assert "wal_size_bytes_after" in data


def test_post_system_maintenance(client):
    """驗證 POST /api/v1/system/maintenance 執行健康維護"""
    resp = client.post(
        "/api/v1/system/maintenance",
        json={"max_backups": 3, "retention_days": 90, "dry_run": True}
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("operation") == "database_maintenance"
    assert data.get("status") in ["passed", "warning"]
    assert data.get("integrity") == "ok"
    assert "pruning" in data


def test_get_maintenance_receipt(client):
    """驗證 GET /api/v1/system/maintenance/receipt 取得維護收據"""
    resp = client.get("/api/v1/system/maintenance/receipt")
    assert resp.status_code == 200
    data = resp.json()

    assert "has_receipt" in data
    if data["has_receipt"]:
        receipt = data["receipt"]
        assert "operation" in receipt
        assert "integrity" in receipt
