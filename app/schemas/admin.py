"""管理后台的响应模型；对外 camelCase。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AdminLogRow(_CamelModel):
    """日志列表的一行；`openid` 已掩码。"""

    id: int
    created_at: datetime
    path: str
    intent: str | None = None
    emotion: str | None = None
    urgency: str | None = None
    openid_masked: str | None = None
    question: str
    generated_answer: str | None = None
    top_similarity: float | None = None
    is_high_risk: bool
    sensitive_words_hit: list[str] = Field(default_factory=list)
    filter_result: str
    alert_sent: bool


class AdminLogPage(_CamelModel):
    """分页结果；字段名对齐管理后台既有契约。"""

    content: list[AdminLogRow] = Field(default_factory=list)
    total_elements: int
    total_pages: int
    number: int
    size: int


class DailyPoint(_CamelModel):
    day: date
    count: int


class PathCount(_CamelModel):
    path: str
    count: int


class AdminStats(_CamelModel):
    total: int
    daily: list[DailyPoint] = Field(default_factory=list)
    path_distribution: list[PathCount] = Field(default_factory=list)
    rejection_rate: float
    recent_crisis: list[AdminLogRow] = Field(default_factory=list)
    recent_venting: list[AdminLogRow] = Field(default_factory=list)


class FetchNoticesResult(_CamelModel):
    """通知采集结果。"""

    implemented: bool
    message: str
    pages_fetched: int = 0
    total_pages: int = 0
    items_seen: int = 0
    items_skipped_known_url: int = 0
    items_duplicate_content: int = 0
    detail_failed: int = 0
    stored: int = 0
    titles: list[str] = Field(default_factory=list)
