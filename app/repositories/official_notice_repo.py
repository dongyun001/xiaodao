"""官网通知检索的数据访问。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import OfficialNotice


@dataclass(frozen=True)
class NoticeMatch:
    """命中的通知。"""

    id: int
    title: str
    url: str
    published_at: datetime | None
    content: str
    similarity: float


class OfficialNoticeRepository:
    """`official_notice` 的只读检索；采集写入见步骤 8。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def search_by_vector(self, vector: list[float], limit: int) -> list[NoticeMatch]:
        """按余弦距离取最近的通知；similarity = 1 - 距离。"""
        distance = OfficialNotice.embedding.cosine_distance(vector)
        statement = (
            select(OfficialNotice, distance.label("distance"))
            .order_by(distance)
            .limit(limit)
        )
        rows = self._session.execute(statement).all()
        return [
            NoticeMatch(
                id=row.OfficialNotice.id,
                title=row.OfficialNotice.title,
                url=row.OfficialNotice.url,
                published_at=row.OfficialNotice.published_at,
                content=row.OfficialNotice.content,
                similarity=1.0 - float(row.distance),
            )
            for row in rows
        ]

    def existing_urls(self, urls: list[str]) -> set[str]:
        """返回已入库的 url 子集；用于避免重复抓取详情页。"""
        if not urls:
            return set()
        statement = select(OfficialNotice.url).where(OfficialNotice.url.in_(urls))
        return set(self._session.scalars(statement))

    def insert_many(self, rows: list[dict[str, Any]]) -> int:
        """按 `content_hash` 去重写入；已存在的同内容通知跳过。"""
        if not rows:
            return 0
        statement = pg_insert(OfficialNotice).values(rows)
        statement = statement.on_conflict_do_nothing(
            index_elements=[OfficialNotice.content_hash]
        )
        result = self._session.execute(statement)
        self._session.commit()
        return int(result.rowcount or 0)

    def count_all(self) -> int:
        return int(
            self._session.scalar(select(func.count()).select_from(OfficialNotice)) or 0
        )
