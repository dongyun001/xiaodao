"""官网公开通知采集：抓列表 → 抓详情 → 写入 `official_notice`。

目标站点：南阳师范学院官网「通知公告」栏目（`NOTICE_FETCH_BASE_URL`）。
站点结构（2026-10 实测）：

- 列表第 1 页：`index/tzgg.htm`；第 N 页：`index/tzgg/{总页数+1-N}.htm`——**页号是倒序的**，
  且详情链接是相对路径（第 1 页 `../info/...`、翻页后 `../../info/...`），必须用当前页 URL 解析。
- 列表项：`div.i-list ul > li > a > div.date > b(日) + span(YYYY-MM)`、`div.info > h3(标题) + p(摘要)`。
- 详情：标题在 `<title>`（去掉 `-南阳师范学院` 后缀），发布时间在页面文本 `日期: YYYY-MM-DD HH:MM`，
  正文在 `div.v_news_content`（内含 `<style>`，取文本前必须剔除）。

`robots.txt` 返回 404（站点未声明限制）；只抓公开页面，不登录、不绕鉴权、不用浏览器自动化。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.official_notice_repo import OfficialNoticeRepository
from app.services.embedding_service import EmbeddingError, embed_texts

logger = logging.getLogger(__name__)

LIST_PATH = "index/tzgg.htm"
SITE_SUFFIX = "-南阳师范学院"
# HTTP 头必须是 ASCII：这里不能出现中文，否则 httpx 会抛 UnicodeEncodeError。
USER_AGENT = "counselor-bot/0.1 (+public notice fetcher; campus QA system)"
CST = timezone(timedelta(hours=8))  # 站点时间按北京时间显示
DETAIL_DELAY_SECONDS = 0.3
EMBED_BATCH_SIZE = 16

_PUBLISH_RE = re.compile(r"日期[:：]\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?")
_DAY_RE = re.compile(r"^\d{1,2}$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


class NoticeFetchError(RuntimeError):
    """采集失败；调用方只降级记录，不影响其它功能。"""


@dataclass
class ListItem:
    """列表页的一条通知（详情尚未抓取）。"""

    url: str
    title: str
    excerpt: str
    published_at: datetime | None


@dataclass
class NoticeRecord:
    """待入库的通知记录。"""

    title: str
    url: str
    published_at: datetime | None
    content: str
    content_hash: str


@dataclass
class FetchReport:
    """一次采集的结果，供脚本与后台展示。"""

    base_url: str
    pages_requested: int = 0
    pages_fetched: int = 0
    total_pages: int = 0
    items_seen: int = 0
    items_skipped_known_url: int = 0
    items_duplicate_content: int = 0
    detail_failed: int = 0
    stored: int = 0
    dry_run: bool = False
    titles: list[str] = field(default_factory=list)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )


def _get(client: httpx.Client, url: str) -> str:
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NoticeFetchError(f"请求失败：{url}（{type(exc).__name__}）") from exc
    response.encoding = response.encoding or "utf-8"
    return response.text


def parse_total_pages(html: str) -> int:
    """从分页块取最大页号；没有分页块时按 1 页处理。"""
    soup = BeautifulSoup(html, "lxml")
    numbers: list[int] = []
    for span in soup.select("span.p_no"):
        text = span.get_text(strip=True)
        if text.isdigit():
            numbers.append(int(text))
        link = span.find("a")
        if link and link.get_text(strip=True).isdigit():
            numbers.append(int(link.get_text(strip=True)))
    return max(numbers) if numbers else 1


def page_url(base_url: str, page: int, total_pages: int) -> str:
    """第 1 页是栏目页，其余页号倒序：第 N 页对应文件 `{total+1-N}.htm`。"""
    root = base_url.rstrip("/") + "/"
    if page <= 1:
        return root + LIST_PATH
    return f"{root}index/tzgg/{total_pages + 1 - page}.htm"


def parse_list_items(html: str, url: str) -> list[ListItem]:
    """解析列表页条目；相对链接按当前页 URL 解析。"""
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one("div.i-list") or soup
    items: list[ListItem] = []

    for li in container.select("ul > li"):
        link = li.find("a", href=True)
        if link is None:
            continue
        absolute = str(httpx.URL(url).join(link["href"]))
        if "/info/" not in absolute:
            continue

        title_el = li.select_one("div.info h3")
        excerpt_el = li.select_one("div.info p")
        day_el = li.select_one("div.date b")
        month_el = li.select_one("div.date span")

        title = (
            title_el.get_text(" ", strip=True)
            if title_el
            else link.get_text(" ", strip=True)
        )
        if not title:
            continue

        published = _combine_list_date(
            day_el.get_text(strip=True) if day_el else "",
            month_el.get_text(strip=True) if month_el else "",
        )
        items.append(
            ListItem(
                url=absolute,
                title=title,
                excerpt=excerpt_el.get_text(" ", strip=True) if excerpt_el else "",
                published_at=published,
            )
        )

    return items


def _combine_list_date(day: str, month: str) -> datetime | None:
    """列表页把日期拆成「日」和「YYYY-MM」两块，拼成北京时间当天零点。"""
    if not _DAY_RE.match(day):
        return None
    match = _MONTH_RE.match(month)
    if match is None:
        return None
    year, month_number = int(match.group(1)), int(match.group(2))
    try:
        return datetime(year, month_number, int(day), tzinfo=CST).astimezone(UTC)
    except ValueError:
        return None


def parse_detail(
    html: str,
    *,
    fallback_title: str,
    fallback_published: datetime | None,
) -> tuple[str, datetime | None, str]:
    """解析详情页：标题、发布时间、正文纯文本。"""
    soup = BeautifulSoup(html, "lxml")

    title = fallback_title
    if soup.title and soup.title.string:
        raw = soup.title.string.strip()
        title = raw.removesuffix(SITE_SUFFIX)

    published = fallback_published
    match = _PUBLISH_RE.search(soup.get_text(" ", strip=True))
    if match:
        date_part, time_part = match.group(1), match.group(2) or "00:00"
        try:
            published = (
                datetime.fromisoformat(f"{date_part}T{time_part}:00")
                .replace(tzinfo=CST)
                .astimezone(UTC)
            )
        except ValueError:
            published = fallback_published

    body = soup.select_one("div.v_news_content") or soup.select_one("div#vsb_content")
    content = ""
    if body is not None:
        for tag in body.find_all(["style", "script"]):
            tag.decompose()
        content = re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip()

    return title, published, content


def content_hash(content: str) -> str:
    """内容 hash：去空白后做 SHA-256，用于去重（见 docs/踩坑记录.md）。"""
    normalized = re.sub(r"\s+", "", content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _known_urls(urls: list[str]) -> set[str]:
    session = SessionLocal()
    try:
        return OfficialNoticeRepository(session).existing_urls(urls)
    finally:
        session.close()


def _store(records: list[NoticeRecord]) -> int:
    if not records:
        return 0
    vectors = _embed(records)
    rows = [
        {
            "title": record.title,
            "url": record.url,
            "published_at": record.published_at,
            "content": record.content,
            "content_hash": record.content_hash,
            "embedding": vector,
        }
        for record, vector in zip(records, vectors, strict=True)
    ]
    session = SessionLocal()
    try:
        return OfficialNoticeRepository(session).insert_many(rows)
    finally:
        session.close()


def _embed(records: list[NoticeRecord]) -> list[list[float]]:
    """通知的 embedding 文本 = 标题 + 正文。"""
    texts = [f"{record.title}\n{record.content}" for record in records]

    async def _run_all() -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            vectors.extend(await embed_texts(texts[start : start + EMBED_BATCH_SIZE]))
        return vectors

    try:
        return asyncio.run(_run_all())
    except EmbeddingError as exc:
        raise NoticeFetchError(f"通知 embedding 失败：{exc}") from exc


def fetch_and_store(
    *,
    pages: int | None = None,
    dry_run: bool = False,
) -> FetchReport:
    """抓取前 N 页通知并入库；`dry_run` 只解析，不抓详情以外的写操作、不算向量、不写库。"""
    settings = get_settings()
    base_url = settings.notice_fetch_base_url.strip()
    if not base_url:
        raise NoticeFetchError("NOTICE_FETCH_BASE_URL 未配置")

    requested = pages if pages is not None else settings.notice_fetch_pages
    report = FetchReport(base_url=base_url, pages_requested=requested, dry_run=dry_run)

    with _client(settings.notice_fetch_timeout_ms / 1000) as client:
        first_url = page_url(base_url, 1, 1)
        first_html = _get(client, first_url)
        report.pages_fetched = 1

        total_pages = parse_total_pages(first_html)
        report.total_pages = total_pages

        items = parse_list_items(first_html, first_url)
        effective_pages = max(1, min(requested, total_pages))
        for page in range(2, effective_pages + 1):
            url = page_url(base_url, page, total_pages)
            html = _get(client, url)
            report.pages_fetched += 1
            items.extend(parse_list_items(html, url))

        report.items_seen = len(items)
        logger.info(
            "列表抓取完成：%d 页（共 %d 页），条目 %d 条",
            report.pages_fetched,
            total_pages,
            report.items_seen,
        )

        known = set() if dry_run else _known_urls([item.url for item in items])
        fresh = [item for item in items if item.url not in known]
        report.items_skipped_known_url = len(items) - len(fresh)

        records: list[NoticeRecord] = []
        seen_hashes: set[str] = set()
        for index, item in enumerate(fresh):
            if index:
                time.sleep(DETAIL_DELAY_SECONDS)
            try:
                html = _get(client, item.url)
            except NoticeFetchError as exc:
                report.detail_failed += 1
                logger.warning("详情抓取失败，跳过：%s", exc)
                continue

            title, published, content = parse_detail(
                html, fallback_title=item.title, fallback_published=item.published_at
            )
            if not content:
                report.detail_failed += 1
                logger.warning("详情正文为空，跳过：%s", item.url)
                continue

            digest = content_hash(content)
            if digest in seen_hashes:
                report.items_duplicate_content += 1
                continue
            seen_hashes.add(digest)
            records.append(
                NoticeRecord(
                    title=title,
                    url=item.url,
                    published_at=published,
                    content=content,
                    content_hash=digest,
                )
            )
            report.titles.append(title)

    if dry_run:
        logger.info("dry-run：待入库 %d 条，不计算 embedding、不写库", len(records))
        return report

    report.stored = _store(records)
    logger.info("入库完成：新增 %d 条", report.stored)
    return report
