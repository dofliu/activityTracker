"""`omni demo` 示範資料集的契約（ADR-031，TODO E1）。

- fail-closed：目標路徑是實機／目前作用中的家目錄時一律拒絕，不清空、不覆寫。
- 目標目錄存在但不是 omni demo 建立的（沒有旗標檔）時同樣拒絕。
- 旗標檔是 `demo_mode` 判定唯一的依據；示範資料要涵蓋一個「近一週持續在動」與
  一個「前一週活躍、近一週歸零」的專案，讓模式感知提案有材料可挑。
- 驗收中心與問候卡在 `demo_mode` 為真時，機器判定不得被示範資料餵綠。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

import main
from core.demo_dataset import (
    DEMO_PROJECTS,
    DemoSafetyError,
    default_demo_home,
    resolve_demo_target,
    seed_demo_home,
)
from core.models import AIPromptEvent, Base, FileActivityEvent, GitActivityEvent
from core.runtime_paths import demo_marker_path, is_demo_home


# ---------------------------------------------------------------- 路徑安全


def test_resolve_demo_target_defaults_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("OMNICONTEXT_HOME", raising=False)
    assert resolve_demo_target(None) == default_demo_home()
    assert resolve_demo_target(None) == (tmp_path / ".omnicontext-demo").resolve()


def test_resolve_demo_target_refuses_ambient_omnicontext_home(tmp_path, monkeypatch):
    real_home = tmp_path / "real-home"
    monkeypatch.setenv("OMNICONTEXT_HOME", str(real_home))
    with pytest.raises(DemoSafetyError):
        resolve_demo_target(str(real_home))


def test_resolve_demo_target_refuses_source_checkout_root(tmp_path, monkeypatch):
    fake_checkout = tmp_path / "checkout"
    monkeypatch.delenv("OMNICONTEXT_HOME", raising=False)
    monkeypatch.setattr("core.demo_dataset.source_checkout_root", lambda: fake_checkout)
    with pytest.raises(DemoSafetyError):
        resolve_demo_target(str(fake_checkout))


def test_resolve_demo_target_refuses_default_omnicontext_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("OMNICONTEXT_HOME", raising=False)
    with pytest.raises(DemoSafetyError):
        resolve_demo_target(str(tmp_path / "OmniContext"))


def test_resolve_demo_target_accepts_a_fresh_unrelated_path(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNICONTEXT_HOME", raising=False)
    target = resolve_demo_target(str(tmp_path / "anything-else"))
    assert target == (tmp_path / "anything-else").resolve()


# ---------------------------------------------------------------- 灌資料


def _counts_from_db(db_path: Path) -> dict[str, int]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        factory = sessionmaker(bind=engine)
        session = factory()
        try:
            return {
                "git": session.query(func.count(GitActivityEvent.id)).scalar(),
                "ai": session.query(func.count(AIPromptEvent.id)).scalar(),
                "file": session.query(func.count(FileActivityEvent.id)).scalar(),
            }
        finally:
            session.close()
    finally:
        engine.dispose()


def test_seed_demo_home_writes_marker_and_events(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNICONTEXT_HOME", raising=False)
    target = tmp_path / "demo-home"
    result = seed_demo_home(home=target, refresh_project_states=False)

    assert result["home"] == str(target)
    assert is_demo_home(target) is True
    assert demo_marker_path(target).is_file()

    expected = {"git": 0, "ai": 0, "file": 0}
    for project in DEMO_PROJECTS:
        expected["git"] += len(project.commits)
        expected["ai"] += len(project.ai_turns)
        expected["file"] += len(project.file_events)
    assert result["counts"] == expected
    assert _counts_from_db(Path(result["database"])) == expected


def test_seed_demo_home_covers_active_and_neglected_projects(tmp_path):
    target = tmp_path / "demo-home"
    result = seed_demo_home(home=target, refresh_project_states=False)
    now = datetime.now()

    engine = create_engine(f"sqlite:///{Path(result['database']).as_posix()}")
    try:
        factory = sessionmaker(bind=engine)
        session = factory()
        try:
            aurora_latest = (
                session.query(func.max(GitActivityEvent.timestamp))
                .filter(GitActivityEvent.repo_name == "aurora-notes")
                .scalar()
            )
            lighthouse_latest = (
                session.query(func.max(GitActivityEvent.timestamp))
                .filter(GitActivityEvent.repo_name == "lighthouse-api")
                .scalar()
            )
        finally:
            session.close()
    finally:
        engine.dispose()

    # aurora-notes 在近一週（含今天）持續在動；lighthouse-api 最後活動要落在
    # 前一週（8～14 天前），近一週完全沒有——這是「被冷落的專案」訊號要的形狀。
    assert (now - aurora_latest).days <= 1
    assert 8 <= (now - lighthouse_latest).days <= 14


def test_seed_demo_home_refuses_existing_non_demo_directory(tmp_path):
    target = tmp_path / "demo-home"
    target.mkdir()
    sentinel = target / "not_mine.txt"
    sentinel.write_text("real data", encoding="utf-8")

    with pytest.raises(DemoSafetyError):
        seed_demo_home(home=target, refresh_project_states=False)

    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "real data"


def test_seed_demo_home_reseed_is_idempotent(tmp_path):
    target = tmp_path / "demo-home"
    first = seed_demo_home(home=target, refresh_project_states=False)
    second = seed_demo_home(home=target, refresh_project_states=False)

    assert first["counts"] == second["counts"]
    assert is_demo_home(target) is True


# ---------------------------------------------------------------- demo_mode 傳播


def test_is_demo_home_false_for_a_plain_directory(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_demo_home(plain) is False


def test_health_check_reports_demo_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICONTEXT_HOME", str(tmp_path))
    from core.api.system import health_check

    assert health_check()["demo_mode"] is False
    demo_marker_path(tmp_path).write_text('{"is_demo": true}', encoding="utf-8")
    assert health_check()["demo_mode"] is True


# ---------------------------------------------------------------- 驗收中心與問候卡的 fail-closed 判準


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class TempDatabase:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def test_acceptance_report_demo_mode_downgrades_passed_items(tmp_path, monkeypatch):
    from core.acceptance import build_acceptance_report
    from core.models import CoverageLedgerInterval
    from datetime import timedelta

    now = datetime(2026, 9, 23, 14, 0)
    db = TempDatabase()
    cfg = DictConfig({"exporters": {"reports_dir": str(tmp_path / "reports")}})
    start = datetime.combine((now - timedelta(days=1)).date(), datetime.min.time())
    with db.session_scope() as session:
        session.add(CoverageLedgerInterval(
            collector="window_watcher",
            started_at=start,
            last_heartbeat_at=start + timedelta(days=1),
            heartbeat_count=288,
            closed_at=start + timedelta(days=1),
        ))

    monkeypatch.setattr("core.acceptance.report.is_demo_home", lambda: False)
    baseline = build_acceptance_report(database=db, cfg=cfg, now=now, only=["A1"])
    baseline_item = baseline["items"][0]
    assert baseline_item["status"] == "passed"
    assert baseline["demo_mode"] is False

    monkeypatch.setattr("core.acceptance.report.is_demo_home", lambda: True)
    demo_report = build_acceptance_report(database=db, cfg=cfg, now=now, only=["A1"])
    demo_item = demo_report["items"][0]
    assert demo_report["demo_mode"] is True
    assert demo_item["status"] == "needs_human"
    assert "示範模式" in demo_item["detail"]


def test_greeting_carries_demo_mode_flag(monkeypatch):
    from core.secretary import greeting as greeting_module

    db = TempDatabase()
    cfg = DictConfig({"proactive_secretary": {"greeting": {"display_name": "Dof"}}})
    now = datetime(2026, 9, 23, 10, 0)

    monkeypatch.setattr(greeting_module, "is_demo_home", lambda: False)
    baseline = greeting_module.build_greeting(database=db, cfg=cfg, now=now, use_llm=False)
    assert baseline["demo_mode"] is False

    monkeypatch.setattr(greeting_module, "is_demo_home", lambda: True)
    demo_greeting = greeting_module.build_greeting(database=db, cfg=cfg, now=now, use_llm=False)
    assert demo_greeting["demo_mode"] is True


# ---------------------------------------------------------------- CLI 接線


def test_cli_demo_subcommand_dispatches_to_cmd_demo(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "cmd_demo", lambda home=None: calls.append(home))
    monkeypatch.setattr("sys.argv", ["main.py", "demo", "--home", "/tmp/example-demo"])
    main.main()
    assert calls == ["/tmp/example-demo"]
