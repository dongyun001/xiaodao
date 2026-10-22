"""ORM 模型集合。"""

from __future__ import annotations

from app.models.base import Base
from app.models.knowledge_base import KnowledgeBase
from app.models.official_notice import OfficialNotice
from app.models.qa_log import QaLog

__all__ = ["Base", "KnowledgeBase", "OfficialNotice", "QaLog"]
