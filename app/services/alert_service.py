"""危机告警：钉钉机器人（加签模式）。

约束：
- 只实现钉钉，不做企业微信；
- 日志与异常信息里**不打印 webhook 与 secret**；
- 告警发送必须在 `qa_log` 写入之前完成：`qa_log` 按 AGENTS 只允许 INSERT，
  所以 `alert_sent` 必须写入时就确定，不能事后 UPDATE。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.qa_log_repo import QaLogRepository
from app.services.llm_service import load_resource, render

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 5.0
QUESTION_EXCERPT_CHARS = 50


def is_configured() -> bool:
    return bool(get_settings().dingtalk_webhook.strip())


def sign(secret: str, timestamp_ms: str) -> str:
    """钉钉加签：base64(HMAC-SHA256(secret, "timestamp\\nsecret"))，再做 URL 编码。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    return quote_plus(base64.b64encode(digest).decode("ascii"))


def signed_url(webhook: str, secret: str, timestamp_ms: str | None = None) -> str:
    """拼出带 timestamp 与 sign 的完整地址；未配置 secret 时原样返回。"""
    if not secret:
        return webhook
    timestamp = timestamp_ms or str(int(time.time() * 1000))
    return f"{webhook}&timestamp={timestamp}&sign={sign(secret, timestamp)}"


def _build_text(
    *, openid: str | None, question: str, path: str, keyword: str, is_test: bool
) -> str:
    template = load_resource("alert-template")
    excerpt = question.strip().replace("\n", " ")[:QUESTION_EXCERPT_CHARS]
    return render(
        str(template["content"]),
        test_marker="（测试）" if is_test else "",
        alert_time=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        openid=openid or "（未提供）",
        path=path,
        keyword=keyword or "（未记录）",
        question_excerpt=excerpt or "（空）",
    )


async def send_alert(
    *,
    openid: str | None,
    question: str,
    path: str,
    keyword: str = "",
    is_test: bool = False,
) -> tuple[bool, str]:
    """发送告警，返回 (是否成功, 说明)。

    钉钉的 HTTP 200 不代表成功，必须看响应体里的 `errcode == 0`。
    """
    settings = get_settings()
    webhook = settings.dingtalk_webhook.strip()
    if not webhook:
        return False, "DINGTALK_WEBHOOK 未配置"

    url = signed_url(webhook, settings.dingtalk_secret.strip())
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": str(load_resource("alert-template").get("title") or "危机告警"),
            "text": _build_text(
                openid=openid,
                question=question,
                path=path,
                keyword=keyword,
                is_test=is_test,
            ),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        # 不把 URL（含 token/签名）写进日志
        return False, f"请求失败：{type(exc).__name__}"

    if response.status_code >= 400:
        return False, f"钉钉返回 HTTP {response.status_code}"

    try:
        body = response.json()
    except ValueError:
        return False, "钉钉响应不是 JSON"

    errcode = body.get("errcode")
    if errcode != 0:
        # errmsg 是钉钉给的错误说明，不含我们的密钥，可以记录
        return False, f"钉钉 errcode={errcode} errmsg={str(body.get('errmsg'))[:80]}"

    return True, "发送成功"


def recent_alert_exists(openid: str, minutes: int) -> bool:
    """去重：同一 openid 在窗口内是否已经成功告警过（查 qa_log，只读）。

    用数据库而不是进程内字典：多 worker 部署时内存计数各算各的，会重复告警。
    """
    if not openid or minutes <= 0:
        return False
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    session = SessionLocal()
    try:
        return QaLogRepository(session).has_recent_alert(openid=openid, since=since)
    finally:
        session.close()
