"""测试基础设施。

集成测试**必须**连 PostgreSQL + pgvector（AGENTS 禁止用 SQLite 替代）：
- 数据库不可用时明确 skip，不静默通过；
- 每个用例前清空三张表，避免用例间互相污染；
- 测试库名默认 `counselor_bot_test`，可用 `TEST_DB_NAME` 覆盖。

环境变量必须在导入 `app.*` 之前设置：`app.database` 在导入时就创建 engine。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]

_ENV_FILE = dotenv_values(REPO_ROOT / ".env")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "counselor_bot_test")

# 其余 DB_* 从 .env 补齐后再切库名；顺序很重要，见模块 docstring。
for _key in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"):
    if _key not in os.environ and _ENV_FILE.get(_key):
        os.environ[_key] = str(_ENV_FILE[_key])
os.environ["DB_NAME"] = TEST_DB_NAME

_CONNECT_PARAMS = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "user": os.environ.get("DB_USER", "counselor"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def _ensure_database() -> None:
    """连到维护库，缺测试库就建一个；连不上直接抛异常。"""
    import psycopg2
    from psycopg2 import sql

    connection = psycopg2.connect(
        dbname="postgres", connect_timeout=5, **_CONNECT_PARAMS
    )
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME))
                )
    finally:
        connection.close()


def _migrate() -> None:
    """对测试库跑迁移（走 Alembic，不建 create_all）。"""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def database() -> None:
    """准备测试库；不可用时跳过所有需要它的用例。"""
    try:
        _ensure_database()
        _migrate()
    except Exception as exc:  # noqa: BLE001 - 建库/迁移任何一步失败都按"数据库不可用"跳过
        pytest.skip(
            f"PostgreSQL/pgvector 不可用，跳过集成测试：{type(exc).__name__}: {exc}"
        )


@pytest.fixture
def db(database: None) -> None:
    """需要数据库的用例：先清空业务表，保证互不影响。"""
    from sqlalchemy import text

    from app.database import engine

    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE qa_log, knowledge_base, official_notice RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def client():
    """FastAPI 测试客户端；进入上下文会跑 lifespan。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
