"""LLM 调用与 Prompt 渲染。

日志不打印学生原话全文、不打印密钥、不打印供应商原始响应。
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).resolve().parents[1] / "resources"
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_MAX_TOKENS = 1000


class LLMError(RuntimeError):
    """LLM 调用失败；上层降级为稳定话术，不透传供应商响应。"""


@lru_cache
def load_resource(name: str) -> dict[str, Any]:
    """读取 `app/resources/<name>.yml`；进程内缓存，改文件需重启。"""
    path = RESOURCES_DIR / f"{name}.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def render(template: str, **values: str) -> str:
    """Prompt 渲染：只用 `str.replace`，模板里含 JSON 花括号，不能用 `str.format`。"""
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


async def chat_completion(
    system: str,
    user: str,
    *,
    timeout: float,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """调用 DeepSeek 的 OpenAI 兼容 chat 接口，返回正文；失败抛 `LLMError`。"""
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise LLMError("DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise LLMError("LLM 请求失败") from exc
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code >= 400:
                raise LLMError(f"LLM 返回 {response.status_code}")

            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise LLMError("LLM 响应解析失败") from exc

            if not isinstance(content, str) or not content.strip():
                # 推理模型可能把 max_tokens 全用在 reasoning 上（见 docs/踩坑记录.md）。
                raise LLMError("LLM 返回空内容")

            return content.strip()

    raise LLMError("LLM 请求失败")
