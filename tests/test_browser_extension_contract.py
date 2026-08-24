import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "watchers" / "browser_extension"


def test_mv3_background_uses_alarm_heartbeat_and_never_logs_prompt_preview():
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    background = (EXTENSION_DIR / "background.js").read_text(encoding="utf-8")

    assert "alarms" in manifest["permissions"]
    assert "/api/v1/extension/heartbeat" in background
    assert "chrome.alarms.create" in background
    assert "OMNICONTEXT_HEARTBEAT_NOW" in background
    assert "setInterval" not in background
    assert "prompt_text" not in " ".join(
        line for line in background.splitlines() if "console." in line
    )


def test_each_content_script_reports_ready_without_logging_prompt_content():
    for script_path in sorted((EXTENSION_DIR / "content_scripts").glob("*.js")):
        source = script_path.read_text(encoding="utf-8")
        assert "OMNICONTEXT_CONTENT_READY" in source
        assert "OMNICONTEXT_CAPTURE_DIAGNOSTIC" in source
        assert "substring(0, 50)" not in source


def test_popup_exposes_heartbeat_and_capture_diagnostics_without_token_echo():
    popup_html = (EXTENSION_DIR / "popup.html").read_text(encoding="utf-8")
    popup_js = (EXTENSION_DIR / "popup.js").read_text(encoding="utf-8")

    assert 'type="password"' in popup_html
    assert "heartbeat-text" in popup_html
    assert "capture-text" in popup_html
    assert "OMNICONTEXT_HEARTBEAT_NOW" in popup_js
    assert "innerText = token" not in popup_js
