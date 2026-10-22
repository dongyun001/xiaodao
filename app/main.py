"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.routers import admin, qa, wechat
from app.services.notice_fetch_service import NoticeFetchError, fetch_and_store

logger = logging.getLogger(__name__)
settings = get_settings()

scheduler: AsyncIOScheduler | None = None


async def _scheduled_notice_fetch() -> None:
    """定时采集官网通知；失败只记日志，不影响服务。"""
    try:
        report = await run_in_threadpool(fetch_and_store, pages=None, dry_run=False)
    except NoticeFetchError as exc:
        logger.error("定时通知采集失败：%s", exc)
        return
    logger.info(
        "定时通知采集完成：列表 %d 条，新增 %d 条", report.items_seen, report.stored
    )


def _start_scheduler() -> AsyncIOScheduler | None:
    """按 `NOTICE_FETCH_CRON`（APScheduler 5 段，UTC）启动定时采集。

    未配置目标站点时直接不启动——没有站点，调度只会每 6 小时刷一次错误日志。
    **多 worker 部署时每个进程都会启动一份，会重复采集**，见 `docs/踩坑记录.md`。
    """
    cron = settings.notice_fetch_cron.strip()
    if not cron or not settings.notice_fetch_base_url.strip():
        logger.info("未配置 NOTICE_FETCH_BASE_URL / NOTICE_FETCH_CRON，跳过定时采集")
        return None

    instance = AsyncIOScheduler(timezone="UTC")
    instance.add_job(
        _scheduled_notice_fetch,
        CronTrigger.from_crontab(cron, timezone="UTC"),
        id="fetch_notices",
        max_instances=1,
        coalesce=True,
    )
    instance.start()
    logger.info("定时通知采集已启动：cron=%s（UTC）", cron)
    return instance


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动/关闭钩子；不在此处建表、不在此处连外部服务。"""
    global scheduler
    logger.info(
        "starting %s on %s:%s",
        settings.app_name,
        settings.server_host,
        settings.server_port,
    )
    scheduler = _start_scheduler()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None
    logger.info("stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(qa.router)
app.include_router(wechat.router)
app.include_router(admin.router)
