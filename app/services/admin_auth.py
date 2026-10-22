"""管理后台认证：HTTP Basic + 登录失败锁定（5 次 / 10 分钟）。

⚠️ 计数器是**进程内内存**的：多 worker 部署时每个进程各算一份，
   锁定会失效（见 `docs/踩坑记录.md`「多 worker 会让 APScheduler 和内存状态失效」）。
   上线前要换成 Redis 或数据库计数。

支持两种 `ADMIN_PASSWORD`：明文（本地开发）或 bcrypt 哈希（以 `$2` 开头，推荐）。
"""

from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.context import CryptContext

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_FAILURES = 5
WINDOW_SECONDS = 600
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Basic"}

security = HTTPBasic(auto_error=False)
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_failures: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _prune(key: str) -> deque[float]:
    attempts = _failures[key]
    now = time.monotonic()
    while attempts and now - attempts[0] > WINDOW_SECONDS:
        attempts.popleft()
    return attempts


def is_locked(request: Request) -> bool:
    """同一来源地址在窗口内失败次数是否已达上限。"""
    return len(_prune(_client_ip(request))) >= MAX_FAILURES


def _record_failure(request: Request) -> int:
    attempts = _prune(_client_ip(request))
    attempts.append(time.monotonic())
    return len(attempts)


def _password_ok(password: str, expected: str) -> bool:
    if expected.startswith("$2"):
        try:
            return _pwd_context.verify(password, expected)
        except ValueError:
            return False
    return hmac.compare_digest(password, expected)


def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> str:
    """FastAPI 依赖：校验 Basic 认证，返回用户名。

    `HTTPBasic(auto_error=False)` 下，缺少 Authorization 头时 `credentials` 为 `None`，
    由本函数自己决定返回 401（而不是让 FastAPI 直接抛）。
    """
    settings = get_settings()

    if not settings.admin_username or not settings.admin_password:
        logger.error("管理后台未配置 ADMIN_USERNAME / ADMIN_PASSWORD，拒绝访问")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理后台未配置凭据",
        )

    if is_locked(request):
        logger.warning("管理后台来源已被锁定：%s", _client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要认证",
            headers=_WWW_AUTHENTICATE,
        )

    user_ok = hmac.compare_digest(credentials.username, settings.admin_username)
    pass_ok = _password_ok(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        failures = _record_failure(request)
        logger.warning("管理后台认证失败（第 %d/%d 次）", failures, MAX_FAILURES)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers=_WWW_AUTHENTICATE,
        )

    return credentials.username
