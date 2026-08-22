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

        self._session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self._engine
        )

    def _ensure_columns(self, conn):
        """自動遷移補齊欄位"""
        try:
            res = conn.execute(text("PRAGMA table_info(ai_prompt_events);")).fetchall()
            existing_cols = {row[1] for row in res}
            if existing_cols and "cwd" not in existing_cols:
                conn.execute(text("ALTER TABLE ai_prompt_events ADD COLUMN cwd TEXT;"))
        except Exception:
            pass

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
