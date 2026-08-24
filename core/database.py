import sqlite3
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .config import get_config
from .models import Base


class Database:
    _instance = None
    _engine = None
    _session_factory = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance.init_db()
        return cls._instance

    def init_db(self) -> None:
        cfg = get_config()
        db_path_str = cfg.get("database.db_path", "omni_context.db")
        
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            root_dir = Path(__file__).parent.parent
            db_path = root_dir / db_path

        db_path.parent.mkdir(parents=True, exist_ok=True)

        db_url = f"sqlite:///{db_path.as_posix()}"
        self._engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True
        )

        # 啟用 SQLite WAL 模式與最佳化鎖定機制
        with self._engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.execute(text("PRAGMA synchronous=NORMAL;"))

            # 自動為現有表加入新欄位 (若尚未存在)
            self._ensure_columns(conn)

        # 建立所有定義之資料表 (包含 project_states, open_loops 等)
        Base.metadata.create_all(bind=self._engine)
        with self._engine.connect() as conn:
            self._ensure_indexes(conn)

        self._session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self._engine
        )

    def _ensure_columns(self, conn):
        """Additive compatibility migration；正式 release 前將改為版本化 migration。"""
        try:
            self._add_columns_if_missing(
                conn,
                "ai_prompt_events",
                {
                    "cwd": "TEXT",
                    "turn_key": "VARCHAR(128)",
                    "source_path": "VARCHAR(1500)",
                    "source_position": "INTEGER",
                    "response_status": "VARCHAR(50)",
                },
            )
            self._add_columns_if_missing(
                conn,
                "open_loops",
                {
                    "status": "VARCHAR(30) NOT NULL DEFAULT 'open'",
                    "fingerprint": "VARCHAR(64)",
                    "last_seen_at": "DATETIME",
                    "updated_at": "DATETIME",
                    "resolution_note": "TEXT",
                },
            )
            if self._table_exists(conn, "open_loops"):
                conn.execute(text(
                    "UPDATE open_loops SET status = CASE "
                    "WHEN resolved_at IS NULL THEN 'open' ELSE 'resolved' END "
                    "WHERE status IS NULL OR status = ''"
                ))
                conn.execute(text(
                    "UPDATE open_loops SET last_seen_at = COALESCE(last_seen_at, created_at), "
                    "updated_at = COALESCE(updated_at, created_at)"
                ))
            conn.commit()
        except Exception as exc:
            raise RuntimeError(f"Database compatibility migration failed: {exc}") from exc

    @staticmethod
    def _add_columns_if_missing(conn, table_name: str, columns: dict[str, str]) -> None:
        rows = conn.execute(text(f"PRAGMA table_info({table_name});")).fetchall()
        existing = {row[1] for row in rows}
        if not existing:
            return
        for column_name, declaration in columns.items():
            if column_name not in existing:
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration};"
                ))

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        return conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        ).first() is not None

    @staticmethod
    def _ensure_indexes(conn) -> None:
        """`create_all` 不會替既有表補 index，因此明確建立 idempotency 約束。"""
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_prompt_events_turn_key "
                "ON ai_prompt_events(turn_key) WHERE turn_key IS NOT NULL;"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_ingestion_checkpoints_source_path "
                "ON ingestion_checkpoints(source_path);"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_ai_prompt_events_response_status "
                "ON ai_prompt_events(response_status);"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_open_loops_status ON open_loops(status);"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_open_loops_fingerprint ON open_loops(fingerprint);"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_open_loops_last_seen_at ON open_loops(last_seen_at);"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_milestone_receipt_date_threshold_channel "
                "ON milestone_notification_receipts(local_date, milestone_minutes, channel);"
            ))
            conn.commit()
        except Exception as exc:
            raise RuntimeError(f"Database index migration failed: {exc}") from exc

    def get_session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def session_scope(self):
        """提供交易範圍的上下文管理器"""
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def get_db() -> Database:
    return Database()
