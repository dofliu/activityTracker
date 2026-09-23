"""Resolve packaged read-only assets and writable OmniContext runtime paths."""

from __future__ import annotations

import os
import sysconfig
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _expanded_path(value: str | Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    return Path(expanded)


def source_checkout_root() -> Path | None:
    """Return the checkout root without treating an installed wheel as writable."""
    if (PACKAGE_ROOT / "pyproject.toml").is_file() and (PACKAGE_ROOT / "main.py").is_file():
        return PACKAGE_ROOT
    return None


def application_home() -> Path:
    """Use an explicit home, preserve checkout behavior, else use a user-writable home."""
    override = os.environ.get("OMNICONTEXT_HOME", "").strip()
    if override:
        return _expanded_path(override).resolve()
    checkout = source_checkout_root()
    if checkout is not None:
        return checkout.resolve()
    return (Path.home() / "OmniContext").resolve()


def default_config_path() -> Path:
    override = os.environ.get("OMNICONTEXT_CONFIG", "").strip()
    if override:
        return _expanded_path(override).resolve()
    return application_home() / "config.yaml"


def runtime_data_root() -> Path:
    """Keep relative data beside an explicit config unless OMNICONTEXT_HOME wins."""
    if os.environ.get("OMNICONTEXT_HOME", "").strip():
        return application_home()
    config_override = os.environ.get("OMNICONTEXT_CONFIG", "").strip()
    if config_override:
        return _expanded_path(config_override).resolve().parent
    return application_home()


def resolve_runtime_path(value: str | Path) -> Path:
    path = _expanded_path(value)
    if path.is_absolute():
        return path.resolve()
    return (runtime_data_root() / path).resolve()


def config_template_path() -> Path:
    candidates = (
        PACKAGE_ROOT / "config.example.yaml",
        Path(sysconfig.get_path("data"))
        / "share"
        / "omnicontext"
        / "config.example.yaml",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "OmniContext config.example.yaml asset is missing from source and install data"
    )


DEMO_MARKER_FILENAME = ".omnicontext_demo_marker.json"


def demo_marker_path(home: Path | None = None) -> Path:
    """`omni demo` 標記檔的路徑；預設查目前 home（ADR-031）。"""
    return (home or application_home()) / DEMO_MARKER_FILENAME


def is_demo_home(home: Path | None = None) -> bool:
    """目前 home 是不是 `omni demo` 建立的示範家目錄——只看旗標檔在不在，不用猜的。"""
    return demo_marker_path(home).is_file()


def web_assets_dir() -> Path:
    return PACKAGE_ROOT / "web"


def browser_extension_assets_dir() -> Path:
    return PACKAGE_ROOT / "watchers" / "browser_extension"


def runtime_asset_status() -> dict:
    template = config_template_path()
    web_dir = web_assets_dir()
    extension_dir = browser_extension_assets_dir()
    checks = {
        "config_template": template.is_file(),
        "web_index": (web_dir / "index.html").is_file(),
        "extension_monitor": (web_dir / "extension-monitor.html").is_file(),
        "web_vendor_marked": (web_dir / "vendor" / "marked.min.js").is_file(),
        "extension_manifest": (extension_dir / "manifest.json").is_file(),
        "extension_popup": (extension_dir / "popup.html").is_file(),
    }
    return {
        "application_home": str(application_home()),
        "runtime_data_root": str(runtime_data_root()),
        "config_path": str(default_config_path()),
        "config_template": str(template),
        "web_assets": str(web_dir),
        "browser_extension": str(extension_dir),
        "checks": checks,
        "status": "ok" if all(checks.values()) else "missing_assets",
    }
