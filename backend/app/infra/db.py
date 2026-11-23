"""数据库连接池、会话工厂与建表初始化（基础设施层 Layer 0）。"""

from pathlib import Path
from typing import Any, Dict

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.infra.config import get_settings


def _build_engine_kwargs(database_url: str) -> Dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}, "pool_pre_ping": True}
    return {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30,
    }


settings = get_settings()
if settings.database_url.startswith("sqlite"):
    database_path = settings.database_url.split("///", 1)[-1]
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, **_build_engine_kwargs(settings.database_url))

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    import app.infra.db_tables  # noqa: F401 - 确保所有 ORM 表已注册至 Base.metadata

    Base.metadata.create_all(bind=engine)
