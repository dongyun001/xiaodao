"""管理后台的查询与统计。

只读：调用方只能拿到列表和统计，本模块不提供任何 UPDATE/DELETE 路径。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from app.database import SessionLocal
from app.models import QaLog
from app.repositories.qa_log_repo import QaLogRepository
from app.schemas.admin import (
    AdminLogPage,
    AdminLogRow,
    AdminStats,
    DailyPoint,
    PathCount,
)

logger = logging.getLogger(__name__)

REJECTION_PATHS = ("low_relevance", "empty_section", "intent_normalize_failed")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
STATS_DAYS = 7
RECENT_LIMIT = 5


class InvalidFilter(ValueError):
    """查询参数不合法（如日期格式错误）。"""


def mask_openid(openid: str | None) -> str | None:
    """openid 掩码：保留前 4 位与后 2 位，其余用 * 代替。"""
    if not openid:
        return None
    if len(openid) <= 6:
        return "*" * len(openid)
    return f"{openid[:4]}{'*' * (len(openid) - 6)}{openid[-2:]}"


def _row(log: QaLog) -> AdminLogRow:
    return AdminLogRow(
        id=log.id,
        created_at=log.created_at,
        path=log.path,
        intent=log.intent,
        emotion=log.emotion,
        urgency=log.urgency,
        openid_masked=mask_openid(log.openid),
        question=log.question,
        generated_answer=log.generated_answer,
        top_similarity=log.top_similarity,
        is_high_risk=log.is_high_risk,
        sensitive_words_hit=list(log.sensitive_words_hit or []),
        filter_result=log.filter_result,
        alert_sent=log.alert_sent,
    )


def _parse_day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidFilter(f"{field} 需要 YYYY-MM-DD 格式") from exc


def fetch_log_page(
    *,
    page: int,
    size: int,
    path: str | None = None,
    emotion: str | None = None,
    keyword: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> AdminLogPage:
    """分页查询日志；日期按 UTC 解释（存储即 UTC）。"""
    safe_page = max(page, 0)
    safe_size = min(max(size, 1), MAX_PAGE_SIZE)

    start_day = _parse_day(start_date, "startDate")
    end_day = _parse_day(end_date, "endDate")
    start = (
        datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)
        if start_day
        else None
    )
    # 结束日期含当天：取次日零点作为开区间上界
    end = (
        datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        if end_day
        else None
    )

    session = SessionLocal()
    try:
        rows, total = QaLogRepository(session).list_page(
            page=safe_page,
            size=safe_size,
            path=path or None,
            emotion=emotion or None,
            keyword=keyword or None,
            start=start,
            end=end,
        )
    finally:
        session.close()

    total_pages = (total + safe_size - 1) // safe_size if total else 0
    return AdminLogPage(
        content=[_row(row) for row in rows],
        total_elements=total,
        total_pages=total_pages,
        number=safe_page,
        size=safe_size,
    )


def build_stats() -> AdminStats:
    """统计：总数、近 7 天每日量、path 分布、拒答率、近期危机与情绪记录。"""
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start - timedelta(days=STATS_DAYS - 1)

    session = SessionLocal()
    try:
        repository = QaLogRepository(session)
        total = repository.count_total()
        by_path = repository.count_by_path()
        daily = repository.count_by_day(since=since)
        crisis = repository.recent_by(path="crisis", limit=RECENT_LIMIT)
        venting = repository.recent_by(emotion="venting", limit=RECENT_LIMIT)
    finally:
        session.close()

    rejected = sum(count for name, count in by_path if name in REJECTION_PATHS)
    rejection_rate = round(rejected / total, 4) if total else 0.0

    return AdminStats(
        total=total,
        daily=[DailyPoint(day=day, count=count) for day, count in daily],
        path_distribution=[
            PathCount(path=name, count=count) for name, count in by_path
        ],
        rejection_rate=rejection_rate,
        recent_crisis=[_row(row) for row in crisis],
        recent_venting=[_row(row) for row in venting],
    )
