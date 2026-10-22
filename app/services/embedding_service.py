"""Embedding 调用：异步 httpx、有限重试、维度校验。

日志只记录条数与维度；不打印密钥，也不打印完整向量和待嵌入文本。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
EMBED_BATCH_SIZE = 16


class EmbeddingError(RuntimeError):
    """embedding 调用失败；上层只降级为稳定话术，不透传供应商响应。"""


def _build_endpoint() -> str:
    settings = get_settings()
    return f"{settings.embedding_base_url.rstrip('/')}/embeddings"


def _build_payload(texts: list[str]) -> dict[str, object]:
    settings = get_settings()
    payload: dict[str, object] = {
        "model": settings.embedding_model,
        "input": texts,
    }
    if settings.embedding_dimensions is not None:
        payload["dimensions"] = settings.embedding_dimensions
    return payload


def _validate(vector: object) -> list[float]:
    settings = get_settings()
    if not isinstance(vector, list):
        raise EmbeddingError("embedding 返回格式异常")
    if len(vector) != settings.vector_dimension:
        raise EmbeddingError(
            f"embedding 维度 {len(vector)} 与 VECTOR_DIMENSION "
            f"{settings.vector_dimension} 不一致；换模型必须重建向量列与 HNSW 索引"
        )
    return [float(value) for value in vector]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """把文本批量转成向量；失败抛出 `EmbeddingError`。"""
    if not texts:
        return []

    settings = get_settings()
    if not settings.embedding_api_key:
        raise EmbeddingError("EMBEDDING_API_KEY 未配置")

    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    endpoint = _build_endpoint()
    payload = _build_payload(texts)

    async with httpx.AsyncClient(timeout=settings.embedding_timeout) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise EmbeddingError("embedding 请求失败") from exc
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code >= 400:
                raise EmbeddingError(f"embedding 返回 {response.status_code}")

            try:
                body = response.json()
                items = sorted(body["data"], key=lambda item: item["index"])
                vectors = [_validate(item["embedding"]) for item in items]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError("embedding 响应解析失败") from exc

            logger.info(
                "embedding 完成：%d 条，维度 %d",
                len(vectors),
                settings.vector_dimension,
            )
            return vectors

    raise EmbeddingError("embedding 请求失败")
