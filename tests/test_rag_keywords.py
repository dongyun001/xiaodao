"""检索：关键词切分与融合分（纯函数，不连库）。"""

from __future__ import annotations

from app.repositories.knowledge_base_repo import ClauseMatch
from app.services.rag_service import (
    KEYWORD_ONLY_SCORE,
    MAX_FUSED_SCORE,
    VECTOR_KEYWORD_BONUS,
    extract_keywords,
    fuse_matches,
)


def _match(clause_id: str, similarity: float) -> ClauseMatch:
    return ClauseMatch(
        id=clause_id,
        doc_title="文档",
        chapter="第一章",
        article_num="一",
        article_title=None,
        content="正文",
        similarity=similarity,
    )


def test_extract_keywords_splits_and_drops_short_or_stopwords() -> None:
    assert extract_keywords("请问，宿舍管理，怎么规定？") == ["宿舍管理", "怎么规定"]
    assert extract_keywords("宿舍能用电磁炉吗") == ["宿舍能用电磁炉吗"]
    assert extract_keywords("a，b") == []


def test_extract_keywords_dedupes_and_caps_at_eight() -> None:
    question = "，".join([f"词{i}" for i in range(10)])
    keywords = extract_keywords(question)
    assert len(keywords) == 8
    assert len(set(keywords)) == 8

    assert extract_keywords("宿舍，宿舍，宿舍") == ["宿舍"]


def test_fuse_keyword_only_gets_fixed_score() -> None:
    result = fuse_matches([], [_match("a", 0.0)], top_k=5)
    assert [(item.article_num, item.similarity) for item in result] == [
        ("一", KEYWORD_ONLY_SCORE)
    ]


def test_fuse_both_paths_adds_bonus_and_caps() -> None:
    result = fuse_matches(
        [_match("a", 0.50), _match("b", 0.94)],
        [_match("a", 0.0), _match("b", 0.0)],
        top_k=5,
    )
    scores = {item.similarity for item in result}
    assert round(0.50 + VECTOR_KEYWORD_BONUS, 4) in scores
    assert MAX_FUSED_SCORE in scores  # 0.94 + 0.08 被截到 0.95


def test_fuse_keeps_vector_only_score_and_sorts_desc() -> None:
    result = fuse_matches([_match("a", 0.40), _match("b", 0.60)], [], top_k=5)
    assert [round(item.similarity, 2) for item in result] == [0.60, 0.40]


def test_fuse_truncates_to_top_k() -> None:
    vector = [_match(str(index), 0.5) for index in range(5)]
    assert len(fuse_matches(vector, [], top_k=3)) == 3
