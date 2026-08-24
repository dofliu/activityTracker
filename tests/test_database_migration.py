from sqlalchemy import create_engine, inspect

from core.database import Database
from core.models import Base


def test_fresh_database_compatibility_migration_is_noop_then_indexes_create(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    database = object.__new__(Database)
    with engine.connect() as connection:
        database._ensure_columns(connection)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        database._ensure_indexes(connection)

    inspector = inspect(engine)
    assert "ai_prompt_events" in inspector.get_table_names()
    assert "milestone_notification_receipts" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("ai_prompt_events")}
    assert "ux_ai_prompt_events_turn_key" in indexes
    receipt_indexes = {
        item["name"] for item in inspector.get_indexes("milestone_notification_receipts")
    }
    assert "ux_milestone_receipt_date_threshold_channel" in receipt_indexes
