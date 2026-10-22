"""数据库连接与会话管理。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

database_url = URL.create(
    drivername="postgresql+psycopg2",
    username=_settings.db_user,
    password=_settings.db_password or None,
    host=_settings.db_host,
    port=_settings.db_port,
    database=_settings.db_name,
)

engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """提供请求级数据库会话；不在此处建表或执行迁移。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
