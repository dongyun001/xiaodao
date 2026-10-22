"""管理后台路由：`/admin/**` 全部需要 HTTP Basic；只有只读查询与统计。

**没有任何写/删接口**：`qa_log` 按设计只允许 INSERT 与只读查询。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.schemas.admin import AdminLogPage, AdminStats, FetchNoticesResult
from app.schemas.qa import AlertTestRequest, AlertTestResponse
from app.services import admin_service, alert_service, notice_fetch_service
from app.services.admin_auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "admin"


@router.get("/", include_in_schema=False)
def admin_page() -> FileResponse:
    """管理后台单页；未认证时浏览器会弹 Basic 登录框。"""
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/logs", response_model=AdminLogPage, response_model_by_alias=True)
def list_logs(
    page: int = Query(0, ge=0, description="页码，从 0 开始"),
    size: int = Query(
        admin_service.DEFAULT_PAGE_SIZE, ge=1, le=admin_service.MAX_PAGE_SIZE
    ),
    path: str = Query(""),
    emotion: str = Query(""),
    keyword: str = Query(""),
    start_date: str = Query("", alias="startDate"),
    end_date: str = Query("", alias="endDate"),
) -> AdminLogPage | JSONResponse:
    """日志分页查询；`created_at DESC`，字段 camelCase。"""
    try:
        return admin_service.fetch_log_page(
            page=page,
            size=size,
            path=path,
            emotion=emotion,
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
        )
    except admin_service.InvalidFilter as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.get("/api/stats", response_model=AdminStats, response_model_by_alias=True)
def get_stats() -> AdminStats:
    """统计概览。"""
    return admin_service.build_stats()


@router.post(
    "/api/alert/test", response_model=AlertTestResponse, response_model_by_alias=True
)
async def alert_test(payload: AlertTestRequest) -> AlertTestResponse:
    """发一条测试告警。

    挂在 `/admin` 下是为了复用 Basic 认证：这个接口会真的往授权群发消息，
    不能像 `/api/ask` 那样对公网裸奔。不参与去重、不写 `qa_log`。
    """
    success, message = await alert_service.send_alert(
        openid=payload.openid or None,
        question=payload.msg or "（测试消息）",
        path="alert_test",
        keyword="测试",
        is_test=True,
    )
    return AlertTestResponse(success=success, message=message)


@router.post(
    "/api/fetch-notices",
    response_model=FetchNoticesResult,
    response_model_by_alias=True,
)
async def fetch_notices(
    pages: int = Query(
        0, ge=0, le=20, description="抓取页数，0 表示用 NOTICE_FETCH_PAGES"
    ),
) -> JSONResponse:
    """手动触发官网通知采集；采集失败返回 502，不伪造成功。"""
    try:
        report = await run_in_threadpool(
            notice_fetch_service.fetch_and_store,
            pages=pages or None,
            dry_run=False,
        )
    except notice_fetch_service.NoticeFetchError as exc:
        logger.error("手动通知采集失败：%s", exc)
        return JSONResponse(
            status_code=502,
            content=FetchNoticesResult(implemented=True, message=str(exc)).model_dump(
                by_alias=True
            ),
        )

    return JSONResponse(
        status_code=200,
        content=FetchNoticesResult(
            implemented=True,
            message="采集完成",
            pages_fetched=report.pages_fetched,
            total_pages=report.total_pages,
            items_seen=report.items_seen,
            items_skipped_known_url=report.items_skipped_known_url,
            items_duplicate_content=report.items_duplicate_content,
            detail_failed=report.detail_failed,
            stored=report.stored,
            titles=report.titles[:20],
        ).model_dump(by_alias=True),
    )
