"""知识库检索的数据访问；只封装 SQL，参数化查询，不拼字符串。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeBase


@dataclass(frozen=True)
class ClauseMatch:
    """库中命中条款；`similarity` 仅在向量检索时有值，关键词检索固定为 0。"""

    id: str
    doc_title: str
    chapter: str | None
    article_num: str
    article_title: str | None
    content: str
    similarity: float


class KnowledgeBaseRepository:
    """`knowledge_base` 的只读检索。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_categories(self) -> list[str]:
        """返回库中已有的板块（`category` 去重），用于意图归一化的可选板块。"""
        statement = (
            select(KnowledgeBase.category).distinct().order_by(KnowledgeBase.category)
        )
        return [category for category in self._session.scalars(statement)]

    def search_by_vector(self, vector: list[float], limit: int) -> list[ClauseMatch]:
        """按余弦距离取最近邻；similarity = 1 - 距离。"""
        distance = KnowledgeBase.embedding.cosine_distance(vector)
        statement = (
            select(KnowledgeBase, distance.label("distance"))
            .order_by(distance)
            .limit(limit)
        )
        rows = self._session.execute(statement).all()
        return [
            ClauseMatch(
                id=row.KnowledgeBase.id,
                doc_title=row.KnowledgeBase.doc_title,
                chapter=row.KnowledgeBase.chapter,
                article_num=row.KnowledgeBase.article_num,
                article_title=row.KnowledgeBase.article_title,
                content=row.KnowledgeBase.content,
                similarity=1.0 - float(row.distance),
            )
            for row in rows
        ]

    def search_by_keywords(
        self, keywords: list[str], limit_per_keyword: int
    ) -> list[ClauseMatch]:
        """按关键词对 `content` 做 ILIKE 匹配，每个关键词最多取 `limit_per_keyword` 条。"""
        matches: list[ClauseMatch] = []
        for keyword in keywords:
            statement = (
                select(KnowledgeBase)
                .where(KnowledgeBase.content.ilike(f"%{keyword}%"))
                .limit(limit_per_keyword)
            )
            for clause in self._session.scalars(statement):
                matches.append(
                    ClauseMatch(
                        id=clause.id,
                        doc_title=clause.doc_title,
                        chapter=clause.chapter,
                        article_num=clause.article_num,
                        article_title=clause.article_title,
                        content=clause.content,
                        similarity=0.0,
                    )
                )
        return matches
