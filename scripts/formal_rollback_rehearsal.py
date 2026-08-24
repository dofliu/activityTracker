"""在隔離環境驗證 package + SQLite 的正式 rollback。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, destination: Path) -> None:
    """使用 SQLite online backup，避免直接複製漏掉尚未 checkpoint 的 WAL。"""
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )
    return completed.stdout.strip()


def _runtime_probe(python: Path, *, cwd: Path, env: dict[str, str]) -> dict:
    code = (
        "import json; "
        "from core import __version__; "
        "from core.database import get_db; "
        "from core.migrations import inspect_migration_status; "
        "from core.runtime_paths import resolve_runtime_path; "
        "db=get_db(); p=resolve_runtime_path('omni_context.db'); "
        "result={'version':__version__,'migration':inspect_migration_status(p)}; "
        "db._engine.dispose(); print(json.dumps(result))"
    )
    output = _run([str(python), "-c", code], cwd=cwd, env=env)
    return json.loads(output.splitlines()[-1])


def _fixture(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO ai_prompt_events "
            "(timestamp, platform, prompt_text, project_tag, turn_key, response_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "2026-08-25 00:00:00",
                "rollback_fixture",
                "rollback rehearsal sentinel",
                "rollback-rehearsal",
                "rollback-rehearsal-sentinel",
                "final_candidate",
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT platform, prompt_text, project_tag, turn_key, response_status "
            "FROM ai_prompt_events WHERE turn_key='rollback-rehearsal-sentinel'"
        ).fetchone()
    return {
        "platform": row[0],
        "prompt_text": row[1],
        "project_tag": row[2],
        "turn_key": row[3],
        "response_status": row[4],
    }


def _read_fixture(path: Path) -> dict | None:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT platform, prompt_text, project_tag, turn_key, response_status "
            "FROM ai_prompt_events WHERE turn_key='rollback-rehearsal-sentinel'"
        ).fetchone()
    if not row:
        return None
    return {
        "platform": row[0],
        "prompt_text": row[1],
        "project_tag": row[2],
        "turn_key": row[3],
        "response_status": row[4],
    }


def run_rehearsal(
    previous_wheel: str | Path,
    candidate_wheel: str | Path,
    output_dir: str | Path | None = None,
) -> dict:
    previous = Path(previous_wheel).expanduser().resolve()
    candidate = Path(candidate_wheel).expanduser().resolve()
    if not previous.is_file() or not candidate.is_file():
        raise FileNotFoundError("previous and candidate wheels must exist")

    root = Path(output_dir).expanduser().resolve() if output_dir else Path(
        tempfile.mkdtemp(prefix="omnicontext-rollback-")
    )
    root.mkdir(parents=True, exist_ok=True)
    app_home = root / "application-home"
    app_home.mkdir()
    config = {
        "database": {"db_path": "omni_context.db"},
        "data_lifecycle": {
            "auto_backup_before_migration": True,
            "backups_dir": "backups",
        },
    }
    (app_home / "config.yaml").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    venv = root / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
        check=True,
        timeout=180,
    )
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = os.environ.copy()
    env["OMNICONTEXT_HOME"] = str(app_home)
    env.pop("OMNICONTEXT_CONFIG", None)
    env.pop("PYTHONPATH", None)

    _run(
        [str(python), "-m", "pip", "install", "--no-deps", str(previous)],
        cwd=root,
        env=env,
    )
    initial = _runtime_probe(python, cwd=root, env=env)
    database = app_home / "omni_context.db"
    fixture_before = _fixture(database)
    pre_upgrade_backup = root / "schema4-pre-upgrade.db"
    _sqlite_backup(database, pre_upgrade_backup)
    backup_sha = _sha256(pre_upgrade_backup)

    _run(
        [
            str(python), "-m", "pip", "install", "--force-reinstall",
            "--no-deps", str(candidate),
        ],
        cwd=root,
        env=env,
    )
    upgraded = _runtime_probe(python, cwd=root, env=env)

    _run(
        [
            str(python), "-m", "pip", "install", "--force-reinstall",
            "--no-deps", str(previous),
        ],
        cwd=root,
        env=env,
    )
    # 回復主檔前必須移除 candidate runtime 遺留的 WAL/SHM，否則舊 schema
    # 會在開啟時被新 WAL 重新套用。
    for sidecar in (
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
    ):
        for attempt in range(10):
            try:
                sidecar.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.25)
    shutil.copy2(pre_upgrade_backup, database)
    restored_sha_before_runtime = _sha256(database)
    rolled_back = _runtime_probe(python, cwd=root, env=env)
    fixture_after = _read_fixture(database)

    checks = {
        "previous_runtime_started": initial["version"] == "1.3.0a1",
        "previous_schema_is_4": initial["migration"]["current_version"] == 4,
        "candidate_runtime_started": upgraded["version"] == "1.3.0a2",
        "candidate_schema_is_5": upgraded["migration"]["current_version"] == 5,
        "database_restore_sha_matches": restored_sha_before_runtime == backup_sha,
        "rolled_back_runtime_started": rolled_back["version"] == "1.3.0a1",
        "rolled_back_schema_is_4": rolled_back["migration"]["current_version"] == 4,
        "fixture_preserved": fixture_after == fixture_before,
    }
    receipt = {
        "schema": "omnicontext.formal_rollback_rehearsal.v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "passed" if all(checks.values()) else "failed",
        "root": str(root),
        "previous_wheel": {"path": str(previous), "sha256": _sha256(previous)},
        "candidate_wheel": {"path": str(candidate), "sha256": _sha256(candidate)},
        "pre_upgrade_database": {
            "path": str(pre_upgrade_backup),
            "sha256": backup_sha,
        },
        "initial": initial,
        "upgraded": upgraded,
        "rolled_back": rolled_back,
        "fixture_before": fixture_before,
        "fixture_after": fixture_after,
        "checks": checks,
    }
    receipt_path = root / "formal-rollback-rehearsal.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    receipt["receipt_path"] = str(receipt_path)
    if receipt["status"] != "passed":
        raise RuntimeError(json.dumps(receipt, ensure_ascii=False))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-wheel", required=True)
    parser.add_argument("--candidate-wheel", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    print(json.dumps(run_rehearsal(
        args.previous_wheel,
        args.candidate_wheel,
        args.output_dir,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
