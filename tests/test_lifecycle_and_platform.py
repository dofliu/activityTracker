from core.platform_services import build_clipboard_command, build_open_command
from core.project_engine import clean_open_loop_title, open_loop_fingerprint


def test_open_loop_fingerprint_normalizes_equivalent_titles():
    first = open_loop_fingerprint("activityTracker", "  Implement   checkpoint  ")
    second = open_loop_fingerprint("activitytracker", "implement checkpoint")
    assert first == second
    assert clean_open_loop_title("  Implement   checkpoint  ") == "Implement checkpoint"


def test_open_command_is_argv_not_shell_string(tmp_path):
    command = build_open_command(str(tmp_path), "explorer")
    assert isinstance(command, list)
    assert str(tmp_path.resolve()) in command


def test_clipboard_command_is_cross_platform_argv():
    command = build_clipboard_command()
    assert isinstance(command, list)
    assert command
