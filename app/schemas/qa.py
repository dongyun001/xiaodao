"""`/api/ask` 的请求与响应模型；对外 camelCase。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.schemas.retrieved_clause import RetrievedClause


class AskRequest(BaseModel):
    """请求体：`{"question": "..."}`，也接受 `{"Question": ...}`。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    question: str = ""


class AskResponse(BaseModel):
    """响应体：字段名必须 camelCase，见 docs/接口契约.md。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    question: str
    answer: str
    path: str
    sources: list[RetrievedClause] = Field(default_factory=list)
    top_similarity: float | None = None


class AlertTestRequest(BaseModel):
    """告警测试请求：`msg` 为要发送的内容，`openid` 仅用于在告警里标明对象。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    msg: str = ""
    openid: str = ""


class AlertTestResponse(BaseModel):
    """告警测试响应；`success` 严格等于钉钉的 `errcode == 0`。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool
    message: str
