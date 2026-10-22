"""规则链：按 tests/cases/rule_chain.yml 驱动，断言每个分支的 `path`。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.repositories.official_notice_repo import NoticeMatch
from app.schemas.retrieved_clause import RetrievedClause
from app.services import qa_service
from app.services.intent_service import IntentResult

CASES_PATH = Path(__file__).parent / "cases" / "rule_chain.yml"
CASES: list[dict[str, Any]] = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))[
    "cases"
]


def _clause(similarity: float) -> RetrievedClause:
    return RetrievedClause(
        doc_title="《测试文档》",
        chapter="第一章",
        article_num="一",
        article_title=None,
        content="正文",
        similarity=similarity,
    )


def _notice(similarity: float) -> NoticeMatch:
    return NoticeMatch(
        id=1,
        title="测试通知",
        url="https://example.test/1",
        published_at=None,
        content="通知正文",
        similarity=similarity,
    )


async def _forbidden(*args: Any, **kwargs: Any) -> str:
    raise AssertionError("危机分支不得调用 LLM")


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_rule_chain_case(
    case: dict[str, Any], db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = case["stub"]

    async def fake_intent(question: str, sections: list[str]) -> IntentResult:
        return IntentResult(
            intent="其他",
            emotion=stub["emotion"],
            urgency="normal",
            section=stub["section"],
        )

    async def fake_retrieve(
        question: str, top_k: int | None = None
    ) -> list[RetrievedClause]:
        return [] if stub["kb_top"] is None else [_clause(stub["kb_top"])]

    async def fake_notices(question: str, top_k: int) -> list[NoticeMatch]:
        return [] if stub["notice_top"] is None else [_notice(stub["notice_top"])]

    async def fake_comfort(question: str) -> str:
        return "我在这里，愿意听你说。"

    async def fake_generate(question: str, clauses: list[RetrievedClause]) -> str:
        return "根据依据回答。"

    monkeypatch.setattr(qa_service, "analyze_intent", fake_intent)
    monkeypatch.setattr(qa_service, "retrieve_clauses", fake_retrieve)
    monkeypatch.setattr(qa_service, "search_notices", fake_notices)
    monkeypatch.setattr(qa_service, "_comfort_answer", fake_comfort)
    monkeypatch.setattr(qa_service, "_generate_answer", fake_generate)
    # 告警不在本用例范围内：一律视为未配置，避免测试打真实钉钉
    monkeypatch.setattr(qa_service.alert_service, "is_configured", lambda: False)

    if case.get("llm_allowed") is False:
        monkeypatch.setattr(qa_service, "chat_completion", _forbidden)
        monkeypatch.setattr(qa_service, "_comfort_answer", _forbidden)
        monkeypatch.setattr(qa_service, "_generate_answer", _forbidden)

    result = await qa_service.answer_question(case["question"])

    assert result.path == case["expected"]
    assert result.answer


async def test_crisis_marks_high_risk_and_never_calls_llm(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa_service, "chat_completion", _forbidden)
    monkeypatch.setattr(qa_service, "_comfort_answer", _forbidden)
    monkeypatch.setattr(qa_service, "_generate_answer", _forbidden)
    monkeypatch.setattr(qa_service.alert_service, "is_configured", lambda: False)

    result = await qa_service.answer_question("我不想活了", openid="oCrisisUser")

    assert result.path == "crisis"
    assert result.is_high_risk is True
    assert result.alert_sent is False  # 未配置 webhook


async def test_empty_question_short_circuits(db: None) -> None:
    result = await qa_service.answer_question("   ")
    assert result.path == "empty_question"
    assert result.question == ""
