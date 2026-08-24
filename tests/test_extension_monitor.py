from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.extension_monitor import build_extension_status
from core.models import AIPromptEvent, Base


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


def test_extension_status_separates_enabled_observed_and_pairing(tmp_path):
    database = TempDatabase(tmp_path / "extension.db")
    cfg = DictConfig(
        {
            "server": {"port": 8765},
            "security": {"browser_extension_ingest_token": "secret-token"},
            "watchers": {
                "browser": {
                    "chatgpt": True,
                    "gemini": False,
                    "claude_web": True,
                    "manus": True,
                }
            },
        }
    )
    with database.session_scope() as session:
        session.add(
            AIPromptEvent(
                timestamp=datetime(2026, 8, 24, 12, 0),
                platform="chatgpt",
                url="https://chatgpt.com/c/example",
                prompt_text="test",
                turn_key="browser-turn",
                response_status="partial",
            )
        )
        # CLI source 不得被誤算成 Browser Extension event。
        session.add(
            AIPromptEvent(
                timestamp=datetime(2026, 8, 24, 12, 1),
                platform="claude",
                url=None,
                source_path="C:/Users/example/.claude/session.jsonl",
                prompt_text="test cli",
                turn_key="cli-turn",
                response_status="final_candidate",
            )
        )

    unpaired = build_extension_status(
        database=database, cfg=cfg, now=datetime(2026, 8, 24, 13, 0)
    )
    paired = build_extension_status(
        "secret-token", database=database, cfg=cfg, now=datetime(2026, 8, 24, 13, 0)
    )

    chatgpt = next(item for item in paired["platforms"] if item["key"] == "chatgpt")
    gemini = next(item for item in paired["platforms"] if item["key"] == "gemini")
    assert unpaired["extension"]["pairing_verified"] is False
    assert paired["extension"]["pairing_verified"] is True
    assert paired["extension"]["events_total"] == 1
    assert chatgpt["enabled"] is True and chatgpt["events_total"] == 1
    assert gemini["enabled"] is False and gemini["events_total"] == 0
    assert "secret-token" not in str(paired)
