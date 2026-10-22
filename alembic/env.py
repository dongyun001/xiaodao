"""Alembic 迁移环境：连接信息与元数据都来自应用配置。"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_settings = get_settings()

_database_url = (
    "postgresql+psycopg2://"
    f"{_settings.db_user}:{_settings.db_password}"
    f"@{_settings.db_host}:{_settings.db_port}/{_settings.db_name}"
)

# alembic.ini 里的 sqlalchemy.url 是占位值，实际连接串只来自环境变量。
# ConfigParser 会把 % 当插值符，密码中的 % 需要转义。
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只输出 SQL，不连接数据库。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
