"""问答相关路由；只做校验与调用，业务在 service 层。"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.schemas.qa import AskRequest, AskResponse
from app.services.qa_service import answer_question

router = APIRouter(prefix="/api", tags=["qa"])


@router.get(
    "/health",
    response_class=PlainTextResponse,
    summary="健康检查",
)
def health() -> str:
    """返回服务存活状态。"""
    return "OK"


@router.post(
    "/ask",
    response_model=AskResponse,
    response_model_by_alias=True,
    summary="学生提问",
)
async def ask(payload: AskRequest) -> AskResponse:
    """执行规则链；异常已在 service 边界降级为稳定话术。"""
    result = await answer_question(payload.question)
    return AskResponse(
        question=result.question,
        answer=result.answer,
        path=result.path,
        sources=result.sources,
        top_similarity=result.top_similarity,
    )
