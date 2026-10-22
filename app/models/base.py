"""SQLAlchemy 声明基类。"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类；不使用 create_all 建表，建表走 Alembic。"""
