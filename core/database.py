from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .config import get_config
from .migrations import upgrade_sqlite_database
from .runtime_paths import resolve_runtime_path


class Database:
    _instance = None
    _engine = None
    _session_factory = None
    _migration_receipt = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance.init_db()
        return cls._instance

    def init_db(self) -> None:
        cfg = get_config()
        db_path = resolve_runtime_path(
            cfg.get("database.db_path", "omni_context.db")
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)

        backup_dir = resolve_runtime_path(
            cfg.get("data_lifecycle.backups_dir", "~/OmniContext/backups")
        )
        self._migration_receipt = upgrade_sqlite_database(
            db_path,
            backup_before=bool(
                cfg.get("data_lifecycle.auto_backup_before_migration", True)
            ),
            backup_dir=backup_dir,
        )

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

        self._session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self._engine
        )

    @property
    def migration_receipt(self) -> dict | None:
        return self._migration_receipt

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
