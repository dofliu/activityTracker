"""Verify wheel/sdist contents and emit a content-only release receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path


WHEEL_REQUIRED_SUFFIXES = (
    "core/runtime_paths.py",
    "scripts/verify_installed_package.py",
    "web/index.html",
    "web/extension-monitor.html",
    "web/app.js",
    "web/style.css",
    "watchers/browser_extension/manifest.json",
    "watchers/browser_extension/popup.html",
    "watchers/browser_extension/popup.js",
    "watchers/browser_extension/content_scripts/chatgpt.js",
    ".data/data/share/omnicontext/config.example.yaml",
    ".dist-info/entry_points.txt",
)

SDIST_REQUIRED_SUFFIXES = (
    "/MANIFEST.in",
    "/config.example.yaml",
    "/core/runtime_paths.py",
    "/scripts/verify_installed_package.py",
    "/web/index.html",
    "/web/extension-monitor.html",
    "/watchers/browser_extension/manifest.json",
    "/docs/USAGE.md",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_required(names: list[str], suffixes: tuple[str, ...]) -> dict[str, bool]:
    normalized = [name.replace("\\", "/") for name in names]
    return {
        suffix: any(name.endswith(suffix) for name in normalized)
        for suffix in suffixes
    }


def verify_artifacts(dist_dir: str | Path) -> dict:
    root = Path(dist_dir).expanduser().resolve()
    wheels = sorted(root.glob("*.whl"))
    sdists = sorted(root.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(
            f"Expected exactly one wheel and one sdist in {root}; "
            f"found wheels={len(wheels)}, sdists={len(sdists)}"
        )

    wheel = wheels[0]
    sdist = sdists[0]
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()

    wheel_checks = _check_required(wheel_names, WHEEL_REQUIRED_SUFFIXES)
    sdist_checks = _check_required(sdist_names, SDIST_REQUIRED_SUFFIXES)
    forbidden = {
        "wheel_config_yaml": any(name.endswith("/config.yaml") for name in wheel_names),
        "wheel_database": any(name.endswith((".db", ".sqlite", ".sqlite3")) for name in wheel_names),
        "sdist_config_yaml": any(name.endswith("/config.yaml") for name in sdist_names),
        "sdist_database": any(name.endswith((".db", ".sqlite", ".sqlite3")) for name in sdist_names),
    }
    passed = all(wheel_checks.values()) and all(sdist_checks.values()) and not any(
        forbidden.values()
    )
    return {
        "receipt_version": 1,
        "operation": "release_artifact_content_verification",
        "status": "passed" if passed else "failed",
        "created_at": datetime.now().astimezone().isoformat(),
        "wheel": {
            "name": wheel.name,
            "size_bytes": wheel.stat().st_size,
            "sha256": _sha256(wheel),
            "entries": len(wheel_names),
            "required": wheel_checks,
        },
        "sdist": {
            "name": sdist.name,
            "size_bytes": sdist.stat().st_size,
            "sha256": _sha256(sdist),
            "entries": len(sdist_names),
            "required": sdist_checks,
        },
        "forbidden_private_artifacts": forbidden,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify OmniContext wheel/sdist runtime assets and privacy exclusions"
    )
    parser.add_argument("dist_dir", help="Directory containing one .whl and one .tar.gz")
    parser.add_argument("--receipt", help="Optional JSON receipt output path")
    args = parser.parse_args()

    receipt = verify_artifacts(args.dist_dir)
    if args.receipt:
        receipt_path = Path(args.receipt).expanduser().resolve()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(receipt_path)
        receipt["receipt_path"] = str(receipt_path)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if receipt["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
