"""应用配置：唯一的环境变量读取入口。"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DURATION_RE = re.compile(r"^PT(\d+(?:\.\d+)?)S$", re.IGNORECASE)


class Settings(BaseSettings):
    """从环境变量和本地 .env 读取配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "辅导员 AI 问答机器人"
    app_env: str = "development"
    server_host: str = "0.0.0.0"
    server_port: int = 8081

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "counselor_bot"
    db_user: str = "counselor"
    db_password: str = ""

    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_timeout: float = 60.0
    intent_deepseek_timeout: float = 10.0
    comfort_deepseek_timeout: float = 20.0

    # Embedding 只要求 OpenAI 兼容接口，不绑定具体厂商；旧 SILICONFLOW_* 变量名继续可用。
    embedding_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias=AliasChoices("EMBEDDING_BASE_URL", "SILICONFLOW_BASE_URL"),
    )
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "SILICONFLOW_API_KEY"),
    )
    embedding_model: str = Field(
        default="BAAI/bge-m3",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "SILICONFLOW_EMBEDDING_MODEL"),
    )
    embedding_timeout: float = 30.0
    # 仅对支持 MRL 截断的模型（如 qwen3-embedding、text-embedding-3）生效；
    # 留空则按模型原生维度返回。必须与 VECTOR_DIMENSION 一致，否则入库直接报错。
    embedding_dimensions: int | None = None
    vector_dimension: int = 1024

    wechat_token: str = ""
    wechat_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("WECHAT_APP_ID", "WECHAT_APPID"),
    )
    wechat_app_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WECHAT_APP_SECRET", "WECHAT_APPSECRET"),
    )
    wechat_encoding_aes_key: str = ""

    counselor_contact: str = ""
    counselor_office_hours: str = "工作日 8:30-17:30"

    retrieval_min_score: float = 0.55
    retrieval_top_k: int = 5
    notice_retrieval_min_score: float = 0.55
    notice_retrieval_top_k: int = 5

    notice_fetch_base_url: str = ""
    notice_fetch_pages: int = 3
    notice_fetch_timeout_ms: int = 15000
    notice_fetch_cron: str = "0 */6 * * *"

    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    alert_dedup_minutes: int = 10

    admin_username: str = "admin"
    admin_password: str = ""

    @field_validator("deepseek_base_url")
    @classmethod
    def validate_deepseek_base_url(cls, value: str) -> str:
        """DeepSeek 的 OpenAI 兼容地址必须以 /v1 结尾。"""
        normalized = value.strip().rstrip("/")
        if not normalized.endswith("/v1"):
            raise ValueError("DEEPSEEK_BASE_URL 必须以 /v1 结尾")
        return normalized

    @field_validator(
        "deepseek_timeout",
        "intent_deepseek_timeout",
        "comfort_deepseek_timeout",
        "embedding_timeout",
        mode="before",
    )
    @classmethod
    def parse_duration(cls, value: object) -> float:
        """接受秒数或 ISO-8601 Duration（如 PT60S）。"""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)

        if isinstance(value, str):
            text = value.strip()
            match = _DURATION_RE.fullmatch(text)
            if match:
                return float(match.group(1))
            try:
                return float(text)
            except ValueError as exc:
                raise ValueError("超时必须是秒数或 PTxxS 格式") from exc

        raise ValueError("超时必须是秒数或 PTxxS 格式")


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置实例。"""
    return Settings()
