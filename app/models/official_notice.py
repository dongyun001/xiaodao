"""学校官网公开通知表。"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.models.base import Base

_settings = get_settings()


class OfficialNotice(Base):
    """官网公开通知；按 `content_hash` 去重，重复采集不新增记录。"""

    __tablename__ = "official_notice"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    embedding: Mapped[list[float]] = mapped_column(
        Vector(_settings.vector_dimension), nullable=False
    )

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_official_notice_content_hash"),
        Index("ix_official_notice_published_at", "published_at"),
    )
