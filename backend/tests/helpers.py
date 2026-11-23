"""dc_agent 测试辅助工厂。"""

from pathlib import Path
from typing import Tuple

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.infra.db import Base
import app.infra.db_tables  # noqa: F401


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionCls = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return SessionCls()


def make_wal_db(tmp_path: Path, name: str) -> Tuple[Engine, sessionmaker]:
    db_path = tmp_path / f"{name}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
