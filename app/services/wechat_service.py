"""微信回调：签名校验、消息解析与回复、客服消息发送。

⚠️ 未验证状态：本模块的代码路径尚未用真实公众号报文验证过——
   `docs/踩坑记录.md` 里明确写过"微信 AES/签名必须用真实回调报文实测"。
   本地只用自造报文验证了签名算法、明文解析与 AES 加解密往返，
   拿到测试号后必须补一次真实回调验证。
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from starlette.concurrency import run_in_threadpool
from wechatpy import create_reply, parse_message
from wechatpy.client import WeChatClient
from wechatpy.crypto import WeChatCrypto
from wechatpy.exceptions import InvalidSignatureException, WeChatClientException
from wechatpy.utils import check_signature

from app.config import get_settings
from app.services.llm_service import load_resource, render

logger = logging.getLogger(__name__)

CUSTOMER_MESSAGE_BYTE_LIMIT = 1900
SEGMENT_INTERVAL_SECONDS = 0.3
ENCRYPT_TYPE_AES = "aes"


def _token() -> str:
    return get_settings().wechat_token.strip()


def verify_url_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """GET 校验：`WECHAT_TOKEN` 为空时一律不通过（不放行）。"""
    token = _token()
    if not token:
        return False
    try:
        # wechatpy 的参数顺序是 (token, signature, timestamp, nonce)，token 在最前面。
        check_signature(token, signature, timestamp, nonce)
    except InvalidSignatureException:
        return False
    return True


def _crypto() -> WeChatCrypto:
    settings = get_settings()
    return WeChatCrypto(
        settings.wechat_token,
        settings.wechat_encoding_aes_key,
        settings.wechat_app_id,
    )


def is_secure_mode(encrypt_type: str | None) -> bool:
    return (encrypt_type or "").lower() == ENCRYPT_TYPE_AES


def parse_incoming(
    body: str,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    secure: bool,
) -> Any:
    """解析回调报文；安全模式先解密。失败抛异常，由路由返回空串。"""
    xml = (
        _crypto().decrypt_message(body, msg_signature, timestamp, nonce)
        if secure
        else body
    )
    return parse_message(xml)


def render_reply_xml(message: Any, content: str, *, secure: bool, nonce: str) -> str:
    """构造回复 XML；安全模式下再加密。"""
    reply_xml = create_reply(content, message=message, render=True)
    if not secure:
        return reply_xml
    return _crypto().encrypt_message(reply_xml, nonce)


def split_by_utf8_bytes(
    text: str, limit: int = CUSTOMER_MESSAGE_BYTE_LIMIT
) -> list[str]:
    """按 UTF-8 字节数分段；多段时加 `(n/m)` 前缀。"""
    if not text:
        return [""]

    # 先按 limit 预留前缀空间切一次，再补前缀，避免前缀把段撑过上限。
    reserve = 16
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        char_size = len(char.encode("utf-8"))
        if size + char_size > limit - reserve:
            chunks.append("".join(current))
            current = []
            size = 0
        current.append(char)
        size += char_size
    if current:
        chunks.append("".join(current))

    if len(chunks) == 1:
        return chunks

    total = len(chunks)
    return [f"({index}/{total}) {chunk}" for index, chunk in enumerate(chunks, start=1)]


@lru_cache
def _client() -> WeChatClient | None:
    """客服消息客户端；wechatpy 内部缓存并自动刷新 access_token。"""
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        return None
    return WeChatClient(settings.wechat_app_id, settings.wechat_app_secret)


def render_menu_reply(key: str) -> str | None:
    """菜单 click 事件按 key 回复；没有对应 key 返回 None（不回复）。"""
    settings = get_settings()
    contact = settings.counselor_contact.strip()
    replies = load_resource("wechat-menu").get("replies") or {}
    template = replies.get(key)
    if not template:
        return None
    return render(
        str(template),
        counselor_contact=contact,
        contact_suffix=f"（联系方式：{contact}）" if contact else "",
    )


def render_fixed_reply(key: str) -> str:
    """渲染 `fixed-replies.yml` 里的固定话术。"""
    settings = get_settings()
    contact = settings.counselor_contact.strip()
    replies = load_resource("fixed-replies").get("replies") or {}
    return render(
        str(replies.get(key) or ""),
        counselor_contact=contact,
        contact_suffix=f"（联系方式：{contact}）" if contact else "",
    )


def _send_text_sync(openid: str, text: str) -> None:
    client = _client()
    if client is None:
        logger.warning("微信凭据未配置，跳过客服消息发送（本地开发属正常）")
        return
    for index, chunk in enumerate(split_by_utf8_bytes(text)):
        if index:
            time.sleep(SEGMENT_INTERVAL_SECONDS)  # 段间限速
        client.message.send_text(openid, chunk)


async def send_customer_text(openid: str, text: str) -> None:
    """发送客服消息；未配置凭据或发送失败只记日志，不影响已返回的响应。"""
    try:
        await run_in_threadpool(_send_text_sync, openid, text)
    except WeChatClientException as exc:
        logger.error(
            "客服消息发送失败：%s", getattr(exc, "errcode", type(exc).__name__)
        )
    except Exception:
        logger.exception("客服消息发送异常")


async def process_text_message(openid: str, content: str) -> None:
    """异步处理文本消息：走规则链后发客服消息。"""
    from app.services.qa_service import answer_question

    try:
        result = await answer_question(content, openid=openid)
        answer = result.answer or render_fixed_reply("system_busy")
    except Exception:
        logger.exception("文本消息处理异常")
        answer = render_fixed_reply("system_busy")

    await send_customer_text(openid, answer)
