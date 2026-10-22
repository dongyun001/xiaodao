"""检索结果模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class RetrievedClause(BaseModel):
    """命中条款；对外序列化为 camelCase（`docTitle`、`articleNum` 等）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    doc_title: str
    chapter: str | None = None
    article_num: str
    article_title: str | None = None
    content: str
    similarity: float
