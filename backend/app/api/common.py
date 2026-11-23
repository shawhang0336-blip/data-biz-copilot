"""API 公共依赖（Session 管理）。"""

from app.infra.db import SessionLocal


def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
