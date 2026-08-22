from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine
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
        
        # 如果是相對路徑，以專案根目錄為基準
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            root_dir = Path(__file__).parent.parent
            db_path = root_dir / db_path

        # 確保父目錄存在
        db_path.parent.mkdir(parents=True, exist_ok=True)

        db_url = f"sqlite:///{db_path.as_posix()}"
        self._engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True
        )
        Base.metadata.create_all(bind=self._engine)
        self._session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self._engine
        )

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
