import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

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
    if "test" not in database.lower():
        pytest.fail(
            f"MySQL integration database name must contain 'test' for safety; got {database!r}. "
            "Use a dedicated test database such as ai_customer_service_test."
        )
    return Settings(
        livechat_agent_access_token="unused-for-integration",
        livechat_account_id="unused-for-integration",
        mysql_host=parsed.hostname or "127.0.0.1",
        mysql_port=parsed.port or 3306,
        mysql_user=unquote(parsed.username or "root"),
        mysql_password=unquote(parsed.password or ""),
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


async def assert_mysql_test_database(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DATABASE()")
            row = await cur.fetchone()
    database = row[0] if row else None
    if not database or "test" not in str(database).lower():
        pytest.fail(
            f"Refusing to run integration cleanup against non-test database: {database!r}. "
            "Set MYSQL_TEST_DSN to a dedicated test database."
        )


async def ensure_skip_locked_supported(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute("SELECT id FROM external_commands LIMIT 0 FOR UPDATE SKIP LOCKED")
            except Exception as exc:
                pytest.skip(f"MySQL SKIP LOCKED is not supported by configured database: {exc}")


def run(coro):
    return asyncio.run(coro)
