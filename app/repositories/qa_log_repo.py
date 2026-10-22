"""`qa_log` 写入。

按设计只允许 INSERT 与只读查询：本类**不提供任何** UPDATE/DELETE 方法，
新增方法前先确认没有违反 AGENTS.md 里 `qa_log` 只读的约束。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import QaLog


class QaLogRepository:
    """问答审计日志的写入。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, payload: dict[str, Any]) -> None:
        """写一条审计日志。"""
        self._session.add(QaLog(**payload))
        self._session.commit()

    def _conditions(
        self,
        *,
        path: str | None,
        emotion: str | None,
        keyword: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> list[Any]:
        conditions: list[Any] = []
        if path:
            conditions.append(QaLog.path == path)
        if emotion:
            conditions.append(QaLog.emotion == emotion)
        if keyword:
            # 参数化 ILIKE，不做字符串拼接；也不支持任意 SQL。
            conditions.append(QaLog.question.ilike(f"%{keyword}%"))
        if start is not None:
            conditions.append(QaLog.created_at >= start)
        if end is not None:
            conditions.append(QaLog.created_at < end)
        return conditions

    def list_page(
        self,
        *,
        page: int,
        size: int,
        path: str | None = None,
        emotion: str | None = None,
        keyword: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[QaLog], int]:
        """分页查询；按 `created_at DESC` 排序，同一时间按 id 兜底。"""
        conditions = self._conditions(
            path=path, emotion=emotion, keyword=keyword, start=start, end=end
        )
        total = (
            self._session.scalar(
                select(func.count()).select_from(QaLog).where(*conditions)
            )
            or 0
        )
        statement = (
            select(QaLog)
            .where(*conditions)
            .order_by(QaLog.created_at.desc(), QaLog.id.desc())
            .limit(size)
            .offset(page * size)
        )
        return list(self._session.scalars(statement)), int(total)

    def recent_by(
        self,
        *,
        path: str | None = None,
        emotion: str | None = None,
        limit: int = 5,
    ) -> list[QaLog]:
        """取最近若干条，用于统计里的近期危机 / 近期情绪记录。"""
        conditions = self._conditions(
            path=path, emotion=emotion, keyword=None, start=None, end=None
        )
        statement = (
            select(QaLog)
            .where(*conditions)
            .order_by(QaLog.created_at.desc(), QaLog.id.desc())
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def has_recent_alert(self, *, openid: str, since: datetime) -> bool:
        """去重判断：窗口内是否已有同 openid 的成功危机告警（只读）。"""
        statement = (
            select(func.count())
            .select_from(QaLog)
            .where(
                QaLog.openid == openid,
                QaLog.is_high_risk.is_(True),
                QaLog.alert_sent.is_(True),
                QaLog.created_at >= since,
            )
        )
        return bool(self._session.scalar(statement))

    def count_total(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(QaLog)) or 0)

    def count_by_path(self) -> list[tuple[str, int]]:
        statement = (
            select(QaLog.path, func.count())
            .group_by(QaLog.path)
            .order_by(func.count().desc())
        )
        return [(row[0], int(row[1])) for row in self._session.execute(statement)]

    def count_by_day(self, *, since: datetime) -> list[tuple[Any, int]]:
        """按 UTC 日期聚合；展示时区当前按 UTC 处理，见 docs/数据库.md。"""
        day = func.date(QaLog.created_at)
        statement = (
            select(day, func.count())
            .where(QaLog.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        return [(row[0], int(row[1])) for row in self._session.execute(statement)]
