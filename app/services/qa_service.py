"""规则链：严格按序执行，命中即返回。

顺序与各分支的 `path` 见 `docs/接口契约.md` 第 1 节。
**危机分支永远最先判断，且该分支不得调用 LLM。**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.knowledge_base_repo import KnowledgeBaseRepository
from app.repositories.qa_log_repo import QaLogRepository
from app.schemas.retrieved_clause import RetrievedClause
from app.services import alert_service
from app.services.embedding_service import EmbeddingError
from app.services.intent_service import IntentResult, analyze_intent
from app.services.llm_service import LLMError, chat_completion, load_resource, render
from app.services.notice_service import search_notices
from app.services.rag_service import retrieve_clauses

logger = logging.getLogger(__name__)

PATH_CRISIS = "crisis"
PATH_EMERGENCY = "emergency"
PATH_GENERIC_EMERGENCY = "generic_emergency"
PATH_VENTING = "negative_emotion_venting"
PATH_NEGATIVE = "negative_emotion"
PATH_EMPTY_SECTION = "empty_section"
PATH_NOTICE = "notice"
PATH_NORMAL = "normal"
PATH_LOW_RELEVANCE = "low_relevance"
PATH_EMPTY_QUESTION = "empty_question"
PATH_ERROR = "error"

NOTICE_PREFERRED_MARKERS = (
    "通知",
    "公告",
    "公示",
    "发布",
    "公布",
    "安排",
    "什么时候开始",
    "截止时间",
)
NOTICE_SOFT_SCORE = 0.30
VENTING_MAX_CHARS = 250


@dataclass
class QaResult:
    """规则链统一返回类型；不在分支里拼 HTTP 响应。"""

    question: str
    answer: str
    path: str
    sources: list[RetrievedClause] = field(default_factory=list)
    top_similarity: float | None = None
    intent: str | None = None
    emotion: str | None = None
    urgency: str | None = None
    section: str | None = None
    generated_answer: str | None = None
    is_high_risk: bool = False
    sensitive_words_hit: list[str] = field(default_factory=list)
    filter_result: str = "pass"
    alert_sent: bool = False


def _words(resource: str, key: str) -> tuple[str, ...]:
    data = load_resource(resource)
    return tuple(
        str(word).strip() for word in (data.get(key) or []) if str(word).strip()
    )


def crisis_keywords() -> tuple[str, ...]:
    return _words("crisis-keywords", "keywords")


def exclusion_words() -> tuple[str, ...]:
    return _words("exclusion-words", "words")


def sensitive_words() -> tuple[str, ...]:
    return _words("sensitive-words", "words")


def scenario_rules() -> list[dict[str, Any]]:
    return list(load_resource("emergency-scenarios").get("scenarios") or [])


def declared_sections() -> tuple[str, ...]:
    """学校声明的板块清单（`app/resources/sections.yml`）。"""
    sections = load_resource("sections").get("sections") or []
    return tuple(str(section).strip() for section in sections if str(section).strip())


def _matches(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _render_reply(key: str) -> str:
    """渲染固定话术；未配置 `COUNSELOR_CONTACT` 时不出现空的联系方式括号。"""
    settings = get_settings()
    contact = settings.counselor_contact.strip()
    replies = load_resource("fixed-replies").get("replies") or {}
    template = str(replies.get(key) or "")
    return render(
        template,
        counselor_contact=contact,
        contact_suffix=f"（联系方式：{contact}）" if contact else "",
    )


def _match_scenario(text: str) -> dict[str, Any] | None:
    for scenario in scenario_rules():
        keywords = tuple(str(word).strip() for word in (scenario.get("keywords") or []))
        if keywords and _matches(text, keywords):
            return scenario
    return None


def _build_context(clauses: list[RetrievedClause]) -> str:
    parts: list[str] = []
    for index, clause in enumerate(clauses, start=1):
        chapter = clause.chapter or ""
        parts.append(
            f"[{index}] 《{clause.doc_title}》{chapter}第{clause.article_num}条\n{clause.content}"
        )
    return "\n\n".join(parts)


def _notice_to_clause(match: Any) -> RetrievedClause:
    """把通知映射成 `sources[]` 的形状。

    契约目前没有 url 字段，通知的来源链接暂时无处安放——见 step 5 汇报里的待确认项。
    """
    return RetrievedClause(
        doc_title=match.title,
        chapter=None,
        article_num="",
        article_title=None,
        content=match.content,
        similarity=round(match.similarity, 4),
    )


def _load_sections_sync() -> list[str]:
    session = SessionLocal()
    try:
        return KnowledgeBaseRepository(session).list_categories()
    finally:
        session.close()


def _write_log_sync(payload: dict[str, Any]) -> None:
    session = SessionLocal()
    try:
        QaLogRepository(session).insert(payload)
    finally:
        session.close()


async def _comfort_answer(question: str) -> str:
    settings = get_settings()
    prompt = load_resource("comfort-prompt")
    return await chat_completion(
        str(prompt["system"]),
        render(str(prompt["user"]), user_input=question),
        timeout=settings.comfort_deepseek_timeout,
    )


async def _generate_answer(question: str, clauses: list[RetrievedClause]) -> str:
    settings = get_settings()
    prompt = load_resource("answer-prompt")
    return await chat_completion(
        str(prompt["system"]),
        render(
            str(prompt["user"]),
            context=_build_context(clauses),
            user_input=question,
        ),
        timeout=settings.deepseek_timeout,
    )


def _notice_decided(
    question: str,
    intent: IntentResult,
    knowledge_top: float | None,
    notice_top: float | None,
) -> bool:
    """通知分支：(noticePreferred && noticeSoftValid) || (noticeHigher && noticeValid)。"""
    settings = get_settings()
    preferred = ("通知" in intent.intent) or any(
        marker in question for marker in NOTICE_PREFERRED_MARKERS
    )
    higher = notice_top is not None and (
        knowledge_top is None or notice_top > knowledge_top
    )
    valid = notice_top is not None and notice_top >= settings.notice_retrieval_min_score
    soft_valid = notice_top is not None and notice_top >= NOTICE_SOFT_SCORE
    return (preferred and soft_valid) or (higher and valid)


async def _run_chain(question: str) -> QaResult:
    settings = get_settings()

    # 1. 危机关键词（先屏蔽排除词）——此分支不得调用 LLM
    if _matches(question, crisis_keywords()) and not _matches(
        question, exclusion_words()
    ):
        return QaResult(
            question=question,
            answer=_render_reply("crisis"),
            path=PATH_CRISIS,
            is_high_risk=True,
        )

    # 2. 意图归一化（超时降级，用原问题继续）；模型只能从声明板块里选
    declared = declared_sections()
    filled_sections = await run_in_threadpool(_load_sections_sync)
    intent = await analyze_intent(question, list(declared))

    # 3. 排除词 + emotion=emergency → 降级 normal
    if intent.emotion == "emergency" and _matches(question, exclusion_words()):
        intent.emotion = "normal"
        intent.urgency = "normal"

    # 4. emergency 命中场景 → emergency
    if intent.emotion == "emergency":
        scenario = _match_scenario(question)
        if scenario is not None:
            answer = render(
                str(scenario.get("reply") or ""),
                counselor_contact=settings.counselor_contact,
                contact_suffix=(
                    f"（联系方式：{settings.counselor_contact}）"
                    if settings.counselor_contact
                    else ""
                ),
            )
            return QaResult(
                question=question,
                answer=answer,
                path=PATH_EMERGENCY,
                intent=intent.intent,
                emotion=intent.emotion,
                urgency=intent.urgency,
                section=intent.section,
            )
        # 5. emergency 无场景 → generic_emergency
        return QaResult(
            question=question,
            answer=_render_reply("generic_emergency"),
            path=PATH_GENERIC_EMERGENCY,
            intent=intent.intent,
            emotion=intent.emotion,
            urgency=intent.urgency,
            section=intent.section,
        )

    # 5b. urgency=high → generic_emergency
    if intent.urgency == "high":
        return QaResult(
            question=question,
            answer=_render_reply("generic_emergency"),
            path=PATH_GENERIC_EMERGENCY,
            intent=intent.intent,
            emotion=intent.emotion,
            urgency=intent.urgency,
            section=intent.section,
        )

    # 6. 负面情绪：venting → 只共情（250 字截断）；consult → 共情 + RAG
    comfort_text: str | None = None
    if intent.emotion == "venting":
        comfort_text = await _comfort_answer(question)
        return QaResult(
            question=question,
            answer=comfort_text[:VENTING_MAX_CHARS],
            path=PATH_VENTING,
            generated_answer=comfort_text,
            intent=intent.intent,
            emotion=intent.emotion,
            urgency=intent.urgency,
            section=intent.section,
        )
    if intent.emotion == "consult":
        comfort_text = await _comfort_answer(question)

    # 7. RAG 混合检索（知识库 + 官网通知）+ 路由。
    #    注意：检索必须排在"留空板块"之前——库里没有条款的板块，通知可能正好有依据；
    #    先判空板块会把通知分支挡死（见 docs/踩坑记录.md「留空板块排在通知检索之前」）。
    clauses = await retrieve_clauses(question, settings.retrieval_top_k)
    notices = await search_notices(question, settings.notice_retrieval_top_k)

    knowledge_top = clauses[0].similarity if clauses else None
    notice_top = notices[0].similarity if notices else None

    if _notice_decided(question, intent, knowledge_top, notice_top):
        sources = [_notice_to_clause(match) for match in notices]
        answer = await _generate_answer(question, sources)
        return QaResult(
            question=question,
            answer=answer,
            path=PATH_NOTICE,
            sources=sources,
            top_similarity=notice_top,
            generated_answer=answer,
            intent=intent.intent,
            emotion=intent.emotion,
            urgency=intent.urgency,
            section=intent.section,
            **_sensitive(question),
        )

    # 8. 知识库命中 → 正常生成（见下方步骤 10）
    knowledge_hit = (
        knowledge_top is not None and knowledge_top >= settings.retrieval_min_score
    )

    # 9. 知识库与通知都没有依据：板块真为空 → 留空板块话术；否则按检索不到拒答
    if not knowledge_hit:
        if (
            intent.emotion == "normal"
            and intent.section is not None
            and intent.section not in filled_sections
        ):
            return QaResult(
                question=question,
                answer=_render_reply("empty_section"),
                path=PATH_EMPTY_SECTION,
                intent=intent.intent,
                emotion=intent.emotion,
                urgency=intent.urgency,
                section=intent.section,
            )
        if comfort_text is not None:
            # consult 分支：共情已经安抚，不再追加"没有依据"的机械拒答
            return QaResult(
                question=question,
                answer=comfort_text,
                path=PATH_NEGATIVE,
                intent=intent.intent,
                emotion=intent.emotion,
                urgency=intent.urgency,
                section=intent.section,
                generated_answer=comfort_text,
            )
        return QaResult(
            question=question,
            answer=_render_reply("low_relevance"),
            path=PATH_LOW_RELEVANCE,
            sources=clauses,
            top_similarity=knowledge_top,
            intent=intent.intent,
            emotion=intent.emotion,
            urgency=intent.urgency,
            section=intent.section,
        )

    # 10. 正常生成 + 敏感词记录
    answer = await _generate_answer(question, clauses)
    if comfort_text is not None:
        answer = f"{comfort_text}\n{answer}"
    return QaResult(
        question=question,
        answer=answer,
        path=PATH_NEGATIVE if comfort_text is not None else PATH_NORMAL,
        sources=clauses,
        top_similarity=knowledge_top,
        generated_answer=answer,
        intent=intent.intent,
        emotion=intent.emotion,
        urgency=intent.urgency,
        section=intent.section,
        **_sensitive(question),
    )


def _sensitive(question: str) -> dict[str, Any]:
    """敏感词只记录不拦截：写日志并置 `filter_result=block`，回答照常返回。"""
    hit = [word for word in sensitive_words() if word in question]
    return {
        "sensitive_words_hit": hit,
        "filter_result": "block" if hit else "pass",
    }


async def _persist(result: QaResult, openid: str | None) -> None:
    """每次问答写一条 `qa_log`；写入失败只记日志，不影响返回。"""
    payload: dict[str, Any] = {
        "question": result.question,
        "path": result.path,
        "intent": result.intent,
        "emotion": result.emotion,
        "urgency": result.urgency,
        "openid": openid,
        "retrieved_clauses": [
            clause.model_dump(by_alias=True) for clause in result.sources
        ],
        "top_similarity": result.top_similarity,
        "generated_answer": result.generated_answer,
        "is_high_risk": result.is_high_risk,
        "sensitive_words_hit": result.sensitive_words_hit,
        "filter_result": result.filter_result,
        "alert_sent": result.alert_sent,
    }
    try:
        await run_in_threadpool(_write_log_sync, payload)
    except Exception:
        logger.exception("qa_log 写入失败")


async def _send_crisis_alert(
    question: str, result: QaResult, openid: str | None
) -> bool:
    """危机告警：去重 → 发送。失败只记日志，不影响学生收到的回复。

    必须在 `qa_log` 写入之前调用：`qa_log` 只允许 INSERT，`alert_sent` 写入时就已确定。
    """
    settings = get_settings()

    if not alert_service.is_configured():
        logger.warning("钉钉未配置，危机告警未发送")
        return False

    if openid and await run_in_threadpool(
        alert_service.recent_alert_exists, openid, settings.alert_dedup_minutes
    ):
        logger.info(
            "同 openid 在 %d 分钟窗口内已告警，抑制本次", settings.alert_dedup_minutes
        )
        return False

    matched = next((word for word in crisis_keywords() if word in question), "")
    ok, message = await alert_service.send_alert(
        openid=openid,
        question=question,
        path=result.path,
        keyword=matched,
    )
    if not ok:
        logger.error("危机告警发送失败：%s", message)
    return ok


async def answer_question(question: str, openid: str | None = None) -> QaResult:
    """规则链入口：返回统一 `QaResult`，异常在边界降级。"""
    text = (question or "").strip()

    if not text:
        result = QaResult(
            question="",
            answer=_render_reply("empty_question"),
            path=PATH_EMPTY_QUESTION,
        )
        await _persist(result, openid)
        return result

    try:
        result = await _run_chain(text)
    except (EmbeddingError, LLMError) as exc:
        logger.warning("外部依赖失败，降级为稳定话术：%s", type(exc).__name__)
        result = QaResult(question=text, answer=_render_reply("error"), path=PATH_ERROR)
    except Exception:
        logger.exception("规则链异常，降级为稳定话术")
        result = QaResult(question=text, answer=_render_reply("error"), path=PATH_ERROR)

    if result.is_high_risk:
        result.alert_sent = await _send_crisis_alert(text, result, openid)

    await _persist(result, openid)
    return result
