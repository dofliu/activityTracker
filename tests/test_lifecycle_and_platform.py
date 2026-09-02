import shutil
import sys

import pytest

from core.manager import derive_monitoring_state, window_probe_is_degraded
from core.platform_services import build_clipboard_command, build_open_command
from core.project_engine import clean_open_loop_title, open_loop_fingerprint
from watchers.window_watcher import WindowWatcherService


def test_open_loop_fingerprint_normalizes_equivalent_titles():
    first = open_loop_fingerprint("activityTracker", "  Implement   checkpoint  ")
    second = open_loop_fingerprint("activitytracker", "implement checkpoint")
    assert first == second
    assert clean_open_loop_title("  Implement   checkpoint  ") == "Implement checkpoint"


@pytest.mark.skipif(
    sys.platform not in ("win32", "darwin") and shutil.which("xdg-open") is None,
    reason="此環境沒有 xdg-open（常見於 Linux 容器／CI）；Windows 與 macOS 實機不受影響",
)
def test_open_command_is_argv_not_shell_string(tmp_path):
    command = build_open_command(str(tmp_path), "explorer")
    assert isinstance(command, list)
    assert str(tmp_path.resolve()) in command


def test_clipboard_command_is_cross_platform_argv():
    windows = build_clipboard_command(system="win32")
    macos = build_clipboard_command(system="darwin")
    linux = build_clipboard_command(
        system="linux",
        which=lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None,
    )

    assert windows[0] == "powershell.exe"
    assert macos == ["pbcopy"]
    assert linux == ["/usr/bin/wl-copy"]


def test_monitoring_state_distinguishes_running_from_degraded_collectors():
    watchers = {"window_watcher": True, "agent_log_watcher": True}
    runtime = {"window_watcher": "running", "agent_log_watcher": "running"}
    health = {"window_watcher": "degraded", "agent_log_watcher": "healthy"}

    state, degraded = derive_monitoring_state(True, watchers, runtime, health)

    assert state == "degraded"
    assert degraded == ["window_watcher"]
    assert derive_monitoring_state(False, watchers, runtime, health) == ("stopped", [])


def test_window_probe_requires_sustained_unavailable_state_before_degraded():
    service = WindowWatcherService()
    for _ in range(5):
        service._record_probe(None, None)
    diagnostics = service.get_diagnostics()

    assert diagnostics["state"] == "unavailable"
    assert diagnostics["last_error_code"] == "foreground_unavailable"
    assert window_probe_is_degraded(
        diagnostics,
        interval_seconds=5,
        degraded_after_seconds=30,
    ) is False

    service._record_probe(None, None)
    assert window_probe_is_degraded(
        service.get_diagnostics(),
        interval_seconds=5,
        degraded_after_seconds=30,
    ) is True

    service._record_probe("chrome.exe", "private title must not be exposed")
    recovered = service.get_diagnostics()
    assert recovered["state"] == "healthy"
    assert recovered["consecutive_unavailable"] == 0
    assert "private title" not in str(recovered)
