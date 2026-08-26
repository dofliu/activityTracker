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
    assert "ready_platform_receipts" in background
    assert "setInterval" not in background
    assert "prompt_text" not in " ".join(
        line for line in background.splitlines() if "console." in line
    )


def test_each_content_script_reports_ready_without_logging_prompt_content():
    site_scripts = [
        EXTENSION_DIR / "content_scripts" / name
        for name in ("chatgpt.js", "gemini.js", "claude.js")
    ]
    for script_path in site_scripts:
        source = script_path.read_text(encoding="utf-8")
        assert "platform:" in source
        assert "substring(0, 50)" not in source


def test_shared_capture_core_scopes_submit_and_rejects_previous_response_pairing():
    source = (EXTENSION_DIR / "content_scripts" / "capture_core.js").read_text(
        encoding="utf-8"
    )
    assert "OMNICONTEXT_CONTENT_READY" in source
    assert "OMNICONTEXT_CAPTURE_DIAGNOSTIC" in source
    assert 'document.addEventListener("submit"' in source
    assert "targetIsComposer(event.target)" in source
    assert "current.count > baseline.count" in source
    assert "current.text !== baseline.text" in source


def test_manifest_loads_shared_core_before_each_refactored_site_script():
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    scripts_by_match = {
        entry["matches"][0]: entry["js"] for entry in manifest["content_scripts"]
    }
    for host in ("https://chatgpt.com/*", "https://claude.ai/*"):
        assert scripts_by_match[host][0] == "content_scripts/capture_core.js"


def test_popup_exposes_heartbeat_and_capture_diagnostics_without_token_echo():
    popup_html = (EXTENSION_DIR / "popup.html").read_text(encoding="utf-8")
    popup_js = (EXTENSION_DIR / "popup.js").read_text(encoding="utf-8")

    assert 'type="password"' in popup_html
    assert "heartbeat-text" in popup_html
    assert "capture-text" in popup_html
    assert "OMNICONTEXT_HEARTBEAT_NOW" in popup_js
    assert "innerText = token" not in popup_js
