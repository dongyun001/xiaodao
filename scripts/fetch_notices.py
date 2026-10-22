"""官网通知采集脚本。

用法：
    python scripts/fetch_notices.py --once --dry-run     # 只解析，不写库、不算向量
    python scripts/fetch_notices.py --once               # 抓取并入库
    python scripts/fetch_notices.py --once --pages 1     # 只抓前 1 页
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.notice_fetch_service import NoticeFetchError, fetch_and_store

logger = logging.getLogger("fetch_notices")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="官网公开通知采集")
    parser.add_argument(
        "--once", action="store_true", help="只跑一次然后退出（定时调度由服务负责）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只解析，不写库、不计算 embedding"
    )
    parser.add_argument(
        "--pages", type=int, default=None, help="抓取页数，默认取 NOTICE_FETCH_PAGES"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        report = fetch_and_store(pages=args.pages, dry_run=args.dry_run)
    except NoticeFetchError as exc:
        logger.error("%s", exc)
        return 1

    print(
        f"站点：{report.base_url}\n"
        f"页数：请求 {report.pages_requested} 页，实际抓 {report.pages_fetched} 页（站点共 {report.total_pages} 页）\n"
        f"列表条目：{report.items_seen} 条；已存在跳过 {report.items_skipped_known_url} 条；"
        f"重复内容 {report.items_duplicate_content} 条；详情失败 {report.detail_failed} 条\n"
        f"{'待入库' if report.dry_run else '已入库'}：{len(report.titles) if report.dry_run else report.stored} 条"
    )
    for title in report.titles[:5]:
        print(f"  - {title}")
    if report.dry_run:
        print("（dry-run：没有写库，也没有计算 embedding）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
