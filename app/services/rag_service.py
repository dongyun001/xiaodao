"""混合检索：向量 topK*2 + 关键词 ILIKE，按固定规则融合。

融合规则（分值只调这里，不要在调用方叠加）：
- 只出现在关键词结果的条目：`KEYWORD_ONLY_SCORE`
- 同时命中向量与关键词的条目：`min(MAX_FUSED_SCORE, 向量分 + VECTOR_KEYWORD_BONUS)`
- 只出现在向量结果的条目：保留原向量分
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.knowledge_base_repo import ClauseMatch, KnowledgeBaseRepository
from app.schemas.retrieved_clause import RetrievedClause
from app.services.embedding_service import embed_texts

logger = logging.getLogger(__name__)

KEYWORD_SPLIT_RE = re.compile(r"[，。！？?,!\s]+")
KEYWORD_ONLY_SCORE = 0.35
VECTOR_KEYWORD_BONUS = 0.08
MAX_FUSED_SCORE = 0.95
MIN_KEYWORD_LENGTH = 2
MAX_KEYWORDS = 8
KEYWORD_LIMIT_PER_KEYWORD = 5
VECTOR_LIMIT_FACTOR = 2
STOPWORDS_PATH = Path(__file__).resolve().parents[1] / "resources" / "stopwords.yml"


@lru_cache
def load_stopwords() -> frozenset[str]:
    """读取停用词表；词表变动后需重启进程生效。"""
    raw = yaml.safe_load(STOPWORDS_PATH.read_text(encoding="utf-8")) or {}
    words = raw.get("stopwords") or []
    return frozenset(str(word).strip() for word in words if str(word).strip())


def extract_keywords(question: str) -> list[str]:
    """切词：按标点与空白分割，长度 ≥ 2，去停用词，去重，最多 8 个。"""
    stopwords = load_stopwords()
    keywords: list[str] = []
    for token in KEYWORD_SPLIT_RE.split(question):
        token = token.strip()
        if len(token) < MIN_KEYWORD_LENGTH or token in stopwords:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= MAX_KEYWORDS:
            break
    return keywords


def fuse_matches(
    vector_matches: list[ClauseMatch],
    keyword_matches: list[ClauseMatch],
    top_k: int,
) -> list[RetrievedClause]:
    """按上方规则融合并取前 `top_k` 条。"""
    scores: dict[str, float] = {}
    clauses: dict[str, ClauseMatch] = {}

    for match in vector_matches:
        clauses.setdefault(match.id, match)
        scores[match.id] = match.similarity

    for match in keyword_matches:
        clauses.setdefault(match.id, match)
        if match.id in scores:
            scores[match.id] = min(
                MAX_FUSED_SCORE, scores[match.id] + VECTOR_KEYWORD_BONUS
            )
        else:
            scores[match.id] = KEYWORD_ONLY_SCORE

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
    return [
        RetrievedClause(
            doc_title=clauses[clause_id].doc_title,
            chapter=clauses[clause_id].chapter,
            article_num=clauses[clause_id].article_num,
            article_title=clauses[clause_id].article_title,
            content=clauses[clause_id].content,
            similarity=round(score, 4),
        )
        for clause_id, score in ranked
    ]


def _search_sync(
    vector: list[float],
    keywords: list[str],
    vector_limit: int,
) -> tuple[list[ClauseMatch], list[ClauseMatch]]:
    """同步会话内的两次检索，供线程池调用。"""
    session = SessionLocal()
    try:
        repository = KnowledgeBaseRepository(session)
        return (
            repository.search_by_vector(vector, vector_limit),
            repository.search_by_keywords(keywords, KEYWORD_LIMIT_PER_KEYWORD),
        )
    finally:
        session.close()


async def retrieve_clauses(
    question: str, top_k: int | None = None
) -> list[RetrievedClause]:
    """对问题做混合检索，返回按融合分降序的条款；不做阈值拒答（属于规则链）。"""
    settings = get_settings()
    limit = top_k if top_k is not None else settings.retrieval_top_k
    keywords = extract_keywords(question)

    vectors = await embed_texts([question])
    vector_matches, keyword_matches = await run_in_threadpool(
        _search_sync, vectors[0], keywords, limit * VECTOR_LIMIT_FACTOR
    )

    logger.info(
        "检索完成：向量 %d 条，关键词 %d 条（切出 %d 个关键词）",
        len(vector_matches),
        len(keyword_matches),
        len(keywords),
    )
    return fuse_matches(vector_matches, keyword_matches, limit)
