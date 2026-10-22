"""意图归一化：10s 超时，失败降级继续（用原问题走后续分支）。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.config import get_settings
from app.services.llm_service import LLMError, chat_completion, load_resource, render

logger = logging.getLogger(__name__)

ALLOWED_EMOTIONS = frozenset({"normal", "venting", "consult", "emergency"})
ALLOWED_URGENCIES = frozenset({"normal", "high"})
FALLBACK_INTENT = "其他"
# 模型被要求"都不属于就填 NONE"；代码侧统一把它折算成 section=None。
SECTION_NONE = "NONE"
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z]*")


@dataclass
class IntentResult:
    """归一化结果。

    `section` 只会是板块清单里的值或 `None`：`None` 表示问题不属于任何已声明的板块，
    因此**不触发**留空板块分支。
    """

    intent: str
    emotion: str
    urgency: str
    section: str | None
    degraded: bool = False


def _fallback() -> IntentResult:
    """降级结果：按普通问题继续，不误判成留空板块。"""
    return IntentResult(
        intent=FALLBACK_INTENT,
        emotion="normal",
        urgency="normal",
        section=None,
        degraded=True,
    )


def _parse(raw: str) -> dict[str, object]:
    """从模型输出里取出 JSON 对象；容忍代码块包裹与前后多余文字。"""
    text = _CODE_FENCE_RE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("输出里没有 JSON 对象")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise TypeError("JSON 顶层不是对象")
    return data


async def analyze_intent(question: str, sections: list[str]) -> IntentResult:
    """归一化学生问题；任何异常都降级，不向学生报错。"""
    settings = get_settings()
    prompt = load_resource("intent-prompt")

    try:
        raw = await chat_completion(
            str(prompt["system"]),
            render(
                str(prompt["user"]),
                sections="、".join(sections) if sections else "（暂无）",
                user_input=question,
            ),
            timeout=settings.intent_deepseek_timeout,
        )
        data = _parse(raw)
    except (LLMError, ValueError, TypeError, KeyError) as exc:
        logger.warning("意图归一化失败，降级继续：%s", type(exc).__name__)
        return _fallback()

    emotion = str(data.get("emotion") or "").strip().lower()
    urgency = str(data.get("urgency") or "").strip().lower()
    section_raw = str(data.get("section") or "").strip()
    section = section_raw if section_raw in sections else None

    return IntentResult(
        intent=str(data.get("intent") or FALLBACK_INTENT).strip() or FALLBACK_INTENT,
        emotion=emotion if emotion in ALLOWED_EMOTIONS else "normal",
        urgency=urgency if urgency in ALLOWED_URGENCIES else "normal",
        section=section,
    )
