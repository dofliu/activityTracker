import os
import sys
import gc
import time
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.config import get_config
from core.database import get_db
from core.manager import WatcherManager, get_manager
from core.server import app
from watchers.file_watcher import FileWatcherService
from watchers.git_watcher import GitWatcherService
from watchers.window_watcher import WindowWatcherService
from watchers.agent_log_watcher import AgentLogWatcherService


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path):
    cfg = get_config()
    db_path = tmp_path / "test_omnicontext.db"
    cfg._config_data = {
        "database": {"db_path": str(db_path)},
        "watchers": {
            "file_watcher": {
                "enabled": True,
                "watch_directories": [str(tmp_path)],
                "extensions": [".txt", ".py", ".md"],
                "ignore_patterns": [],
            },
            "git_watcher": {
                "enabled": True,
                "repositories": [str(tmp_path)],
                "scan_interval_seconds": 60,
                "max_depth": 2,
            },
            "window_watcher": {
                "enabled": False,  # 測試環境避免呼叫 Win32 API
            },
            "agent_log_watcher": {
                "enabled": False,
            },
        },
        "synthesizer": {
            "schedule": {"enabled": False},
            "periodic_checkpoint": {"enabled": False},
        },
        "notifiers": {
            "telegram": {"enabled": False},
            "desktop": {"enabled": False},
        },
    }
    db = get_db()

    yield tmp_path

    # Teardown
    gc.collect()


def test_file_watcher_self_healing(tmp_path):
    """驗證 FileWatcher 在 Observer 終止後能透過 check_health_and_heal 自動重啟修復"""
    service = FileWatcherService()
    service.start()
    assert service.observer.is_alive() is True

    # 模擬 Observer 異常終止
    service.observer.stop()
    service.observer.join(timeout=1.0)
    assert service.observer.is_alive() is False

    # 執行自我修復
    heal_result = service.check_health_and_heal()
    assert heal_result.get("healed") is True
    assert heal_result.get("status") == "healed"
    assert service.observer.is_alive() is True

    # 驗證診斷資訊
    diag = service.get_diagnostics()
    assert diag["is_alive"] is True
    assert diag["state"] == "running"
    assert diag["healing_events_count"] >= 1
    assert len(diag["recent_healing_events"]) >= 1

    # 清理
    service.stop()


def test_git_watcher_fault_isolation_and_healing(tmp_path):
    """驗證 GitWatcher 在遇到損壞倉庫時進行局部隔離，不影響其他流程與線程自我修復"""
    # 建立一個假目錄模擬損壞倉庫
    broken_repo = tmp_path / "broken_repo"
    broken_repo.mkdir()
    (broken_repo / ".git").mkdir()  # 沒有有效的 git 結構

    service = GitWatcherService()
    service._cached_repos = [broken_repo]
    service._last_repo_discovery_time = time.time()

    # 執行掃描，應該優雅略過並記錄於 degraded_repos
    service.scan_repositories()

    diag = service.get_diagnostics()
    assert diag["scan_count"] == 1
    assert diag["degraded_repos_count"] == 1
    assert any("broken_repo" in r.get("repo_name", "") for r in diag["degraded_repos"])

    # 測試線程終止後的自我修復
    service._running = False
    service._thread = None
    heal_result = service.check_health_and_heal()
    assert heal_result.get("healed") is True
    assert service._thread is not None
    assert service._thread.is_alive() is True

    service.stop()


def test_manager_supervise_and_heal(tmp_path):
    """驗證 WatcherManager 的 supervise_and_heal 能夠巡檢所有採集器並自動修復"""
    manager = WatcherManager()
    manager.start_all()

    # 模擬 FileWatcher 與 GitWatcher 異常中斷
    manager.file_watcher.observer.stop()
    manager.file_watcher.observer.join(timeout=1.0)
    manager.git_watcher._thread = None

    # 觸發管理員守護修復
    res = manager.supervise_and_heal()
    assert res.get("status") == "healed"
    assert "file_watcher" in res.get("healed_services", [])
    assert "git_watcher" in res.get("healed_services", [])

    status = manager.get_status()
    assert status["self_healing"]["healing_events_count"] >= 1
    assert "file_watcher" in status["collector_diagnostics"]
    assert "git_watcher" in status["collector_diagnostics"]

    manager.stop_all()


def test_system_heal_and_health_api(tmp_path):
    """驗證 /api/v1/system/heal 與 /api/v1/system/health 端點"""
    client = TestClient(app)

    # 測試 health 端點
    health_resp = client.get("/api/v1/system/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert "collector_health" in health_data
    assert "collector_diagnostics" in health_data
    assert "self_healing" in health_data

    # 測試 heal 端點
    heal_resp = client.post("/api/v1/system/heal")
    assert heal_resp.status_code == 200
    heal_data = heal_resp.json()
    assert "status" in heal_data
    assert "healed_services" in heal_data
