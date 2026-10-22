"""官网通知检索。

当前 `official_notice` 表为空（采集是步骤 8），检索会返回空列表，
规则链的通知分支因此不会命中——这是预期行为，不是错误。
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool

from app.database import SessionLocal
from app.repositories.official_notice_repo import NoticeMatch, OfficialNoticeRepository
from app.services.embedding_service import embed_texts

logger = logging.getLogger(__name__)


def _search_sync(vector: list[float], limit: int) -> list[NoticeMatch]:
    session = SessionLocal()
    try:
        return OfficialNoticeRepository(session).search_by_vector(vector, limit)
    finally:
        session.close()


async def search_notices(question: str, top_k: int) -> list[NoticeMatch]:
    """按向量相似度检索官网通知；失败时由调用方降级。"""
    vectors = await embed_texts([question])
    matches = await run_in_threadpool(_search_sync, vectors[0], top_k)
    logger.info("通知检索：命中 %d 条", len(matches))
    return matches
