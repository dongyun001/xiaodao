"""告警：加签、去重、未配置时的降级。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlparse

import pytest

from app.config import get_settings
from app.database import SessionLocal
from app.models import QaLog
from app.services import alert_service


def _independent_signature(secret: str, timestamp: str) -> str:
    """按钉钉文档独立实现一遍，用于锁住我们的加签结果。"""
    payload = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return quote_plus(base64.b64encode(digest).decode("ascii"))


def test_sign_matches_dingtalk_algorithm() -> None:
    secret, timestamp = "SECunitSecretValue", "1700000000000"
    assert alert_service.sign(secret, timestamp) == _independent_signature(
        secret, timestamp
    )


def test_signed_url_contains_timestamp_and_sign() -> None:
    url = alert_service.signed_url(
        "https://example.test/robot/send?access_token=x", "SECabc", "1700000000000"
    )
    query = parse_qs(urlparse(url).query)
    assert query["timestamp"] == ["1700000000000"]
    assert (
        unquote_plus(query["sign"][0])
        == base64.b64encode(
            hmac.new(b"SECabc", b"1700000000000\nSECabc", hashlib.sha256).digest()
        ).decode()
    )


def test_signed_url_without_secret_is_unchanged() -> None:
    webhook = "https://example.test/robot/send?access_token=x"
    assert alert_service.signed_url(webhook, "") == webhook


async def test_send_alert_without_webhook_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "dingtalk_webhook", "")
    success, message = await alert_service.send_alert(
        openid="oX", question="q", path="crisis"
    )
    assert success is False
    assert "DINGTALK_WEBHOOK" in message


def test_recent_alert_exists_only_counts_successful_high_risk(db: None) -> None:
    session = SessionLocal()
    try:
        session.add_all(
            [
                QaLog(
                    question="q1",
                    path="crisis",
                    openid="oSameUser",
                    is_high_risk=True,
                    alert_sent=True,
                    filter_result="pass",
                ),
                QaLog(
                    question="q2",
                    path="crisis",
                    openid="oOtherUser",
                    is_high_risk=True,
                    alert_sent=False,
                    filter_result="pass",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    assert alert_service.recent_alert_exists("oSameUser", 10) is True
    assert (
        alert_service.recent_alert_exists("oOtherUser", 10) is False
    )  # 未成功发送不算已告警
    assert alert_service.recent_alert_exists("oUnknownUser", 10) is False


def test_recent_alert_exists_ignores_expired_window(db: None) -> None:
    from sqlalchemy import update

    session = SessionLocal()
    try:
        session.add(
            QaLog(
                question="old",
                path="crisis",
                openid="oOldUser",
                is_high_risk=True,
                alert_sent=True,
                filter_result="pass",
            )
        )
        session.commit()
        session.execute(
            update(QaLog)
            .where(QaLog.openid == "oOldUser")
            .values(created_at=datetime(2020, 1, 1, tzinfo=UTC))
        )
        session.commit()
    finally:
        session.close()

    assert alert_service.recent_alert_exists("oOldUser", 10) is False
