"""微信回调路由；只做参数校验与调用，业务在 `wechat_service`。

⚠️ 尚未用真实公众号报文验证（见 `app/services/wechat_service.py` 顶部说明）。
"""

from __future__ import annotations

import binascii
import logging
from xml.parsers.expat import ExpatError

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import PlainTextResponse
from wechatpy.exceptions import WeChatException

from app.services import wechat_service

# 回调报文不可信：签名/AES 失败、XML 结构异常、字段类型不对，都按“不回复”处理。
PARSE_ERRORS = (
    WeChatException,
    ExpatError,
    ValueError,
    TypeError,
    KeyError,
    binascii.Error,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["wechat"])

MSG_TYPE_TEXT = "text"
MSG_TYPE_EVENT = "event"
EVENT_SUBSCRIBE = "subscribe"
EVENT_CLICK = "click"


def _xml_response(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")


@router.get(
    "/wechat/callback",
    response_class=PlainTextResponse,
    summary="微信服务器地址校验",
)
def verify_wechat(
    signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query(""),
) -> str:
    """`WECHAT_TOKEN` 为空或签名不通过时返回 `fail`，不放行。"""
    if not wechat_service.verify_url_signature(signature, timestamp, nonce):
        logger.warning("微信 GET 校验失败")
        return "fail"
    return echostr


@router.post(
    "/wechat/callback",
    summary="微信消息与事件回调",
)
async def wechat_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    encrypt_type: str = Query(""),
    msg_signature: str = Query(""),
) -> Response:
    """文本消息立即返回空串并后台处理；关注与菜单点击同步回复 XML；其他事件不回复。"""
    body = (await request.body()).decode("utf-8")
    secure = wechat_service.is_secure_mode(encrypt_type)

    try:
        message = wechat_service.parse_incoming(
            body,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            secure=secure,
        )
    except PARSE_ERRORS:
        logger.warning("微信回调报文解析失败，按不回复处理")
        return Response(content="", media_type="text/plain")

    msg_type = getattr(message, "type", "")
    event = getattr(message, "event", "")

    if msg_type == MSG_TYPE_TEXT:
        openid = getattr(message, "source", "")
        content = getattr(message, "content", "")
        background_tasks.add_task(wechat_service.process_text_message, openid, content)
        return Response(content="", media_type="text/plain")

    if msg_type == MSG_TYPE_EVENT and event == EVENT_SUBSCRIBE:
        reply = wechat_service.render_fixed_reply("welcome")
        return _xml_response(
            wechat_service.render_reply_xml(message, reply, secure=secure, nonce=nonce)
        )

    if msg_type == MSG_TYPE_EVENT and event == EVENT_CLICK:
        key = getattr(message, "key", "")
        reply = wechat_service.render_menu_reply(key)
        if reply:
            return _xml_response(
                wechat_service.render_reply_xml(
                    message, reply, secure=secure, nonce=nonce
                )
            )
        logger.info("菜单 key 没有对应话术，不回复")

    return Response(content="", media_type="text/plain")
