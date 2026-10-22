"""微信：签名校验、AES 加解密往返、客服消息分段。"""

from __future__ import annotations

import hashlib

import pytest
from wechatpy.crypto import WeChatCrypto

from app.config import get_settings
from app.services import wechat_service


def _signature(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()


def test_url_signature_accepts_correct_and_rejects_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "wechat_token", "unit-token")
    timestamp, nonce = "1700000000", "nonce123"

    assert wechat_service.verify_url_signature(
        _signature("unit-token", timestamp, nonce), timestamp, nonce
    )
    assert not wechat_service.verify_url_signature("deadbeef", timestamp, nonce)


def test_empty_token_always_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "wechat_token", "")

    assert not wechat_service.verify_url_signature("anything", "1", "2")


def test_secure_mode_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "wechat_token", "unit-token")
    monkeypatch.setattr(settings, "wechat_app_id", "wxunit0000000001")
    monkeypatch.setattr(settings, "wechat_encoding_aes_key", "a" * 43)

    crypto = WeChatCrypto(
        settings.wechat_token, settings.wechat_encoding_aes_key, settings.wechat_app_id
    )
    plain_xml = (
        "<xml><ToUserName><![CDATA[wxunit0000000001]]></ToUserName>"
        "<FromUserName><![CDATA[oUnitUser]]></FromUserName>"
        "<CreateTime>1700000000</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[宿舍能用电磁炉吗]]></Content><MsgId>1</MsgId></xml>"
    )
    envelope = crypto.encrypt_message(plain_xml, "nonce123")
    signature = hashlib.sha1(
        "".join(
            sorted(
                [
                    settings.wechat_token,
                    _field(envelope, "TimeStamp"),
                    _field(envelope, "Nonce"),
                    _field(envelope, "Encrypt"),
                ]
            )
        ).encode()
    ).hexdigest()

    message = wechat_service.parse_incoming(
        envelope,
        msg_signature=signature,
        timestamp=_field(envelope, "TimeStamp"),
        nonce=_field(envelope, "Nonce"),
        secure=True,
    )

    assert message.type == "text"
    assert message.content == "宿舍能用电磁炉吗"


def _field(envelope: str, name: str) -> str:
    marker = f"<{name}><![CDATA["
    if marker in envelope:
        return envelope.split(marker)[1].split("]]></" + name + ">")[0]
    return envelope.split(f"<{name}>")[1].split(f"</{name}>")[0]


def test_split_by_utf8_bytes_respects_limit_and_prefixes() -> None:
    text = "这是一段用于测试分段的中文内容。" * 300
    chunks = wechat_service.split_by_utf8_bytes(text)

    assert len(chunks) > 1
    assert all(
        len(chunk.encode("utf-8")) <= wechat_service.CUSTOMER_MESSAGE_BYTE_LIMIT
        for chunk in chunks
    )
    assert chunks[0].startswith("(1/")
    assert "".join(chunk.split(" ", 1)[1] for chunk in chunks) == text


def test_split_short_text_stays_single() -> None:
    assert wechat_service.split_by_utf8_bytes("短消息") == ["短消息"]
