import uuid
from datetime import datetime

import aiomysql
import pytest

from app.db.bootstrap import ensure_inbound_events_compat
from app.workers.livechat_idle_timer import LiveChatIdleTimerRepository
from conftest import (
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_idle_repository_detects_cross_thread_customer_activity_from_created_at_mysql():
    mysql_test_config()
    run(_test_idle_repository_detects_cross_thread_customer_activity_from_created_at_mysql())


def test_inbound_events_compat_upgrades_activity_column_as_indexed_virtual_mysql():
    mysql_test_config()
    run(_test_inbound_events_compat_upgrades_activity_column_as_indexed_virtual_mysql())


async def _test_inbound_events_compat_upgrades_activity_column_as_indexed_virtual_mysql():
    test_id = f"idle-activity-upgrade-{uuid.uuid4().hex}"
    settings = await provision_mysql_test_settings()
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("ALTER TABLE inbound_events DROP INDEX idx_inbound_events_chat_activity")
                await cur.execute("ALTER TABLE inbound_events DROP COLUMN effective_activity_at")
                await ensure_inbound_events_compat(cur)

        occurred_at = datetime(2026, 7, 3, 8, 59, 0)
        created_at_fallback = datetime(2026, 7, 3, 9, 1, 0)
        await _insert_inbound_event(
            pool,
            chat_id=f"{test_id}:chat",
            thread_id=f"{test_id}:thread-1",
            dedup_key=f"{test_id}:occurred",
            occurred_at=occurred_at,
            created_at=datetime(2026, 7, 3, 9, 2, 0),
            processed=0,
        )
        await _insert_inbound_event(
            pool,
            chat_id=f"{test_id}:chat",
            thread_id=f"{test_id}:thread-2",
            dedup_key=f"{test_id}:created",
            created_at=created_at_fallback,
            processed=1,
        )

        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT EXTRA, GENERATION_EXPRESSION
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'inbound_events'
                      AND COLUMN_NAME = 'effective_activity_at'
                    """
                )
                column = await cur.fetchone()
                await cur.execute(
                    """
                    SELECT COLUMN_NAME, SEQ_IN_INDEX
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'inbound_events'
                      AND INDEX_NAME = 'idx_inbound_events_chat_activity'
                    ORDER BY SEQ_IN_INDEX
                    """
                )
                index_rows = await cur.fetchall()
                await cur.execute(
                    """
                    SELECT dedup_key, effective_activity_at
                    FROM inbound_events
                    WHERE dedup_key IN (%s, %s)
                    ORDER BY dedup_key
                    """,
                    (f"{test_id}:created", f"{test_id}:occurred"),
                )
                generated_rows = await cur.fetchall()
                explain_sql = """
                    EXPLAIN SELECT id
                    FROM inbound_events
                    WHERE chat_id = %s
                      AND ignored = 0
                      AND sender_role IN ('external', 'customer')
                      AND effective_activity_at > %s
                    LIMIT 1
                """
                await cur.execute(explain_sql, (f"{test_id}:chat", datetime(2026, 7, 3, 9, 0, 0)))
                natural_plan = await cur.fetchone()
                await cur.execute(
                    explain_sql.replace(
                        "FROM inbound_events",
                        "FROM inbound_events FORCE INDEX (idx_inbound_events_chat_activity)",
                    ),
                    (f"{test_id}:chat", datetime(2026, 7, 3, 9, 0, 0)),
                )
                forced_plan = await cur.fetchone()

        assert column is not None
        assert "VIRTUAL GENERATED" in str(column["EXTRA"]).upper()
        generation_expression = str(column["GENERATION_EXPRESSION"]).lower().replace("`", "")
        assert "coalesce(occurred_at,created_at)" in generation_expression.replace(" ", "")
        assert [row["COLUMN_NAME"] for row in index_rows] == [
            "chat_id",
            "ignored",
            "sender_role",
            "effective_activity_at",
        ]
        assert {
            row["dedup_key"]: row["effective_activity_at"]
            for row in generated_rows
        } == {
            f"{test_id}:created": created_at_fallback,
            f"{test_id}:occurred": occurred_at,
        }
        assert "idx_inbound_events_chat_activity" in str(natural_plan["possible_keys"] or "")
        assert forced_plan["key"] == "idx_inbound_events_chat_activity"
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


async def _test_idle_repository_detects_cross_thread_customer_activity_from_created_at_mysql():
    test_id = f"idle-activity-{uuid.uuid4().hex}"
    cutoff = datetime(2026, 7, 3, 9, 0, 0)
    settings = await provision_mysql_test_settings()
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        await _insert_inbound_event(
            pool,
            chat_id=f"{test_id}:chat-1",
            thread_id=f"{test_id}:thread-2",
            dedup_key=f"{test_id}:same-chat",
            created_at=datetime(2026, 7, 3, 9, 1, 0),
            processed=1,
        )
        await _insert_inbound_event(
            pool,
            chat_id=f"{test_id}:chat-2",
            thread_id=f"{test_id}:thread-2",
            dedup_key=f"{test_id}:other-chat",
            created_at=datetime(2026, 7, 3, 9, 2, 0),
            processed=0,
        )
        repository = LiveChatIdleTimerRepository(pool)

        assert await repository.has_customer_activity_after(
            {"chat_id": f"{test_id}:chat-1", "thread_id": f"{test_id}:thread-1"}, cutoff
        )
        assert not await repository.has_customer_activity_after(
            {"chat_id": f"{test_id}:chat-1", "thread_id": f"{test_id}:thread-1"},
            datetime(2026, 7, 3, 9, 1, 0),
        )
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


async def _insert_inbound_event(
    pool,
    *,
    chat_id: str,
    thread_id: str,
    dedup_key: str,
    created_at: datetime,
    processed: int,
    occurred_at: datetime | None = None,
):
    sql = """
    INSERT INTO inbound_events (
      source, raw_action, chat_id, thread_id, event_id, standard_event_type,
      sender_role, occurred_at, dedup_key, payload_json, ignored, processed, created_at
    ) VALUES (
      'livechat_webhook', 'incoming_event', %s, %s, %s, 'MESSAGE_CREATED',
      'external', %s, %s, JSON_OBJECT(), 0, %s, %s
    )
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql,
                (chat_id, thread_id, dedup_key, occurred_at, dedup_key, processed, created_at),
            )
