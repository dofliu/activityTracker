from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.capture_coverage import build_capture_coverage
from core.models import AIPromptEvent, Base, WindowEvent


class TempDatabase:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
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


class FakeConfig:
    def get(self, key, default=None):
        return default


def test_capture_coverage_keeps_focus_web_and_transcript_independent(tmp_path):
    database = TempDatabase(tmp_path / "coverage.db")
    with database.session_scope() as session:
        session.add(
            WindowEvent(
                app_name="Claude.exe",
                window_title="Claude",
                start_time=datetime(2026, 8, 25, 9, 0),
                end_time=datetime(2026, 8, 25, 9, 10),
                duration_seconds=600,
            )
        )
        session.add_all(
            [
                AIPromptEvent(
                    platform="claude",
                    url="https://claude.ai/new",
                    prompt_text="web prompt",
                    timestamp=datetime(2026, 8, 25, 9, 5),
                    turn_key="web-turn",
                ),
                AIPromptEvent(
                    platform="claude_desktop",
                    prompt_text="desktop prompt",
                    response_text="desktop response",
                    source_path="desktop-session.jsonl",
                    timestamp=datetime(2026, 8, 25, 9, 6),
                    turn_key="desktop-turn",
                ),
            ]
        )

    empty_data = tmp_path / "Claude"
    empty_logs = empty_data / "local-agent-mode-sessions"
    result = build_capture_coverage(
        database=database,
        cfg=FakeConfig(),
        now=datetime(2026, 8, 25, 10, 0),
        claude_data_dir=empty_data,
        claude_logs_dir=empty_logs,
    )
    claude = next(item for item in result["platforms"] if item["key"] == "claude")
    gemini = next(item for item in result["platforms"] if item["key"] == "gemini")

    assert claude["desktop_focus"]["state"] == "observed"
    assert claude["desktop_focus"]["foreground_seconds_today"] == 600
    assert claude["web_capture"]["turns_today"] == 1
    assert claude["web_capture"]["responses_today"] == 0
    assert claude["transcript_capture"]["state"] == "observed"
    assert claude["transcript_capture"]["responses_today"] == 1
    assert gemini["web_capture"]["state"] == "waiting"
    assert gemini["transcript_capture"]["state"] == "unsupported"
    assert "desktop-session.jsonl" not in str(result)
    assert "web prompt" not in str(result)


def test_cloud_cache_is_detected_but_never_claimed_as_transcript(tmp_path):
    database = TempDatabase(tmp_path / "cache.db")
    data_dir = tmp_path / "Claude"
    cache = data_dir / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb"
    cache.mkdir(parents=True)

    result = build_capture_coverage(
        database=database,
        cfg=FakeConfig(),
        now=datetime(2026, 8, 25, 10, 0),
        claude_data_dir=data_dir,
        claude_logs_dir=data_dir / "local-agent-mode-sessions",
    )
    claude = next(item for item in result["platforms"] if item["key"] == "claude")
    assert claude["transcript_capture"]["state"] == "cache_detected_unparsed"
    assert claude["transcript_capture"]["turns_today"] == 0
