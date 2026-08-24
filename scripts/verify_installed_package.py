"""Smoke-test an installed OmniContext wheel inside an isolated environment."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from importlib.metadata import version
from pathlib import Path


def verify_install(expected_home: str | Path) -> dict:
    import core
    import main as cli
    from core.data_lifecycle import configured_database_path
    from core.database import get_db
    from core.runtime_paths import (
        application_home,
        default_config_path,
        runtime_asset_status,
    )

    expected = Path(expected_home).expanduser().resolve()
    actual_home = application_home()
    if actual_home != expected:
        raise RuntimeError(f"Application home mismatch: expected={expected}, actual={actual_home}")

    cli.cmd_init([])
    database = get_db()
    migration = database.migration_receipt or {}
    assets = runtime_asset_status()

    from fastapi.testclient import TestClient
    from core.server import app

    client = TestClient(app)
    origin = {"Origin": "http://127.0.0.1:8765"}
    endpoints = {
        "health": client.get("/api/v1/health", headers=origin).status_code,
        "dashboard": client.get("/", headers=origin).status_code,
        "extension_monitor": client.get("/extension-monitor", headers=origin).status_code,
        "static_app_js": client.get("/static/app.js", headers=origin).status_code,
    }

    config_path = default_config_path()
    database_path = configured_database_path()
    package_root = Path(core.__file__).resolve().parent.parent
    checks = {
        "application_home_matches": actual_home == expected,
        "config_created": config_path.is_file(),
        "config_outside_package": package_root not in config_path.parents,
        "database_created": database_path.is_file(),
        "database_outside_package": package_root not in database_path.parents,
        "migration_up_to_date": (
            migration.get("after", {}).get("state") == "up_to_date"
            and migration.get("after", {}).get("current_version")
            == migration.get("after", {}).get("latest_version")
        ),
        "assets_complete": assets["status"] == "ok",
        "endpoints_200": all(code == 200 for code in endpoints.values()),
    }
    passed = all(checks.values())
    return {
        "receipt_version": 1,
        "operation": "installed_package_smoke",
        "status": "passed" if passed else "failed",
        "created_at": datetime.now().astimezone().isoformat(),
        "distribution_version": version("omnicontext"),
        "package_root": str(package_root),
        "application_home": str(actual_home),
        "config_path": str(config_path),
        "database_path": str(database_path),
        "migration": {
            "state": migration.get("after", {}).get("state"),
            "current_version": migration.get("after", {}).get("current_version"),
            "latest_version": migration.get("after", {}).get("latest_version"),
            "applied_now": migration.get("applied_now", []),
        },
        "assets": assets["checks"],
        "endpoints": endpoints,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify installed OmniContext runtime paths, migration, and Web assets"
    )
    parser.add_argument("--expect-home", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()

    receipt = verify_install(args.expect_home)
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
