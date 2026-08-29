"""project_states 重整的並發與 health-read 邊界測試。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from threading import Barrier

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.project_engine as project_engine
from core.models import AIPromptEvent, Base, ProjectState


class TempDatabase:
    """使用檔案型 SQLite，讓多執行緒測試具有與正式環境相同的連線行為。"""

    def __init__(self, path):
        self.engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def test_project_state_count_excludes_bucket_without_refresh(tmp_path, monkeypatch):
    database = TempDatabase(tmp_path / "project_count.db")
    monkeypatch.setattr(project_engine, "get_db", lambda: database)

    with database.session_scope() as session:
        session.add_all([
            ProjectState(project_key="教師評鑑", display_name="教師評鑑"),
            ProjectState(
                project_key="General / Unassigned",
                display_name="General / Unassigned",
            ),
        ])

    assert project_engine.get_project_state_count() == 1


def test_concurrent_project_refresh_creates_one_state_per_key(tmp_path, monkeypatch):
    """同時快取失效時，僅一個請求應執行重整，且唯一鍵不應使任何請求失敗。"""
    database = TempDatabase(tmp_path / "project_refresh.db")
    monkeypatch.setattr(project_engine, "get_db", lambda: database)
    monkeypatch.setattr(project_engine, "_LAST_PROJECT_REFRESH_TIME", 0.0)
    monkeypatch.setattr(project_engine, "_PROJECT_CACHE", [])

    with database.session_scope() as session:
        session.add(
            AIPromptEvent(
                timestamp=datetime(2026, 8, 29, 13, 54, 36),
                platform="codex",
                prompt_text="整理資料夾並進行統計分析",
                project_tag="教師評鑑",
            )
        )

    barrier = Barrier(4)

    def refresh_from_parallel_request():
        barrier.wait()
        project_engine.refresh_project_states()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(refresh_from_parallel_request) for _ in range(4)]
        for future in futures:
            future.result()

    with database.session_scope() as session:
        assert (
            session.query(ProjectState)
            .filter(ProjectState.project_key == "教師評鑑")
            .count()
            == 1
        )
