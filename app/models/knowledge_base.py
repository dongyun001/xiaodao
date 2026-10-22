"""知识库条款表。"""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.models.base import Base

_settings = get_settings()


class KnowledgeBase(Base):
    """一条记录只含一条条款，按 `id` 稳定 upsert。"""

    __tablename__ = "knowledge_base"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    doc_num: Mapped[int | None] = mapped_column(Integer)
    doc_title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    doc_version: Mapped[str | None] = mapped_column("version", String(64))
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(32))

    chapter: Mapped[str | None] = mapped_column(String(255))
    section: Mapped[str | None] = mapped_column(String(255))
    article_num: Mapped[str] = mapped_column(String(64), nullable=False)
    article_title: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float]] = mapped_column(
        Vector(_settings.vector_dimension), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_knowledge_base_article_num", "article_num"),
        Index("ix_knowledge_base_category", "category"),
    )
