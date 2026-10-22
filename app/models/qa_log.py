"""问答审计日志表（只允许 INSERT 与只读查询）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QaLog(Base):
    """每次问答写一条；不提供 UPDATE/DELETE 接口或 ORM 写方法。"""

    __tablename__ = "qa_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32))
    emotion: Mapped[str | None] = mapped_column(String(32))
    urgency: Mapped[str | None] = mapped_column(String(16))
    need_type: Mapped[str | None] = mapped_column(String(32))
    openid: Mapped[str | None] = mapped_column(String(64), index=True)

    retrieved_clauses: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    top_similarity: Mapped[float | None] = mapped_column(Float)
    generated_answer: Mapped[str | None] = mapped_column(Text)
    is_high_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sensitive_words_hit: Mapped[list[str] | None] = mapped_column(JSONB)
    filter_result: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pass"
    )
    alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_qa_log_created_at", "created_at"),
        Index("ix_qa_log_path", "path"),
    )
