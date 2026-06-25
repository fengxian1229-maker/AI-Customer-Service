import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.core.settings import Settings
from app.db.bootstrap import bootstrap_database
from app.db.mysql import create_pool


MYSQL_DSN_ENV_NAMES = ("MYSQL_TEST_DSN", "DATABASE_URL", "AI_CS_TEST_MYSQL_DSN")


@dataclass(frozen=True)
class MysqlTestConfig:
    settings: Settings
    dsn_env_name: str


def mysql_test_config() -> MysqlTestConfig:
    for env_name in MYSQL_DSN_ENV_NAMES:
        value = os.getenv(env_name)
        if value:
            return MysqlTestConfig(settings=settings_from_dsn(value), dsn_env_name=env_name)
    pytest.skip("MySQL integration DSN not configured")


def settings_from_dsn(dsn: str) -> Settings:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql", "mysql+aiomysql"}:
        pytest.skip(f"MySQL integration DSN has unsupported scheme: {parsed.scheme}")
    database = parsed.path.lstrip("/")
    if not database:
        pytest.skip("MySQL integration DSN must include a database name")
    return Settings(
        livechat_agent_access_token="unused-for-integration",
        livechat_account_id="unused-for-integration",
        mysql_host=parsed.hostname or "127.0.0.1",
        mysql_port=parsed.port or 3306,
        mysql_user=parsed.username or "root",
        mysql_password=parsed.password or "",
        mysql_database=database,
    )


async def create_bootstrapped_mysql_pool():
    config = mysql_test_config()
    pool = await create_pool(config.settings)
    try:
        await bootstrap_database(pool, sql_dir=Path("sql"))
        await ensure_skip_locked_supported(pool)
    except Exception:
        pool.close()
        await pool.wait_closed()
        raise
    return pool


async def ensure_skip_locked_supported(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute("SELECT id FROM external_commands LIMIT 0 FOR UPDATE SKIP LOCKED")
            except Exception as exc:
                pytest.skip(f"MySQL SKIP LOCKED is not supported by configured database: {exc}")


def run(coro):
    return asyncio.run(coro)
