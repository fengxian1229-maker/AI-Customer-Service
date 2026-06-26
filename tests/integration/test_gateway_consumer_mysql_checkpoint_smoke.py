import json
import uuid

import aiomysql
import pytest

from app.db.repositories import InboundEventRepository
from app.graph.builder import build_workflow_graph
from app.graph.checkpointing import build_checkpointer
from app.schemas.events import InboundEvent
from app.workers.gateway_consumer import process_next_batch

from conftest import (
    assert_mysql_test_database,
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_gateway_consumer_mysql_checkpoint_mode_runtime_smoke():
    mysql_test_config()
    run(_test_gateway_consumer_mysql_checkpoint_mode_runtime_smoke())


async def _test_gateway_consumer_mysql_checkpoint_mode_runtime_smoke() -> None:
    test_id = f"p5b1-gateway-{uuid.uuid4().hex}"
    chat_id = f"{test_id}:chat"
    conversation_id = f"livechat:{chat_id}"
    settings = await provision_mysql_test_settings(
        langgraph_checkpoint_mode="mysql",
        langgraph_checkpoint_setup_on_start=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    setup_managed = None
    reopened = None
    try:
        await assert_mysql_test_database(pool)

        setup_managed = build_checkpointer("mysql", settings=settings)
        setup_managed.checkpointer.setup()
        setup_managed.close()
        setup_managed = None

        event = InboundEvent(
            source="integration_test",
            raw_action="integration.gateway.message",
            chat_id=chat_id,
            thread_id=f"{test_id}:thread",
            event_id=f"{test_id}:event",
            event_type="message",
            standard_event_type="MESSAGE_CREATED",
            author_id="customer",
            sender_role="external",
            occurred_at="2026-06-26 00:00:00.000000",
            dedup_key=f"integration:{test_id}",
            payload_json={"event": {"text": ""}, "text": ""},
            ignored=False,
        )
        inbound_repository = InboundEventRepository(pool)
        insert_result = await inbound_repository.insert(event)
        assert insert_result == {"inserted": True, "duplicate": False}

        batch_result = await process_next_batch(
            pool,
            limit=1,
            checkpoint_mode="mysql",
            settings=settings,
        )

        assert batch_result["processed"] == 1
        assert batch_result["failed"] == 0
        assert batch_result["enqueued"] == 1

        inbound_row = await fetch_one(
            pool,
            "SELECT id, processed FROM inbound_events WHERE dedup_key = %s",
            (event.dedup_key,),
        )
        assert inbound_row["processed"] == 1
        inbound_event_id = inbound_row["id"]

        conversation_row = await fetch_one(
            pool,
            """
            SELECT conversation_id, chat_id, status, workflow_stage
            FROM conversation_states
            WHERE conversation_id = %s
            """,
            (conversation_id,),
        )
        assert conversation_row["conversation_id"] == conversation_id
        assert conversation_row["chat_id"] == chat_id
        assert conversation_row["status"] == "AI_ACTIVE"
        assert conversation_row["workflow_stage"] is None

        message_rows = await fetch_all(
            pool,
            """
            SELECT inbound_event_id, sender_role, message_type, text_content
            FROM conversation_messages
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (conversation_id,),
        )
        assert len(message_rows) == 1
        assert message_rows[0]["inbound_event_id"] == inbound_event_id
        assert message_rows[0]["sender_role"] == "customer"
        assert message_rows[0]["message_type"] == "text"
        assert message_rows[0]["text_content"] is None

        outbound_rows = await fetch_all(
            pool,
            """
            SELECT id, inbound_event_id, action_type, message_type, status, payload_json
            FROM outbound_messages
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (conversation_id,),
        )
        assert len(outbound_rows) == 1
        assert outbound_rows[0]["inbound_event_id"] == inbound_event_id
        assert outbound_rows[0]["action_type"] == "send_event"
        assert outbound_rows[0]["message_type"] == "text"
        assert outbound_rows[0]["status"] == "PENDING"
        assert outbound_rows[0]["payload_json"]["text"] == "请补充你要咨询的问题，或说明是存款、提款、流水还是需要真人客服。"

        checkpoint_run_rows = await fetch_all(
            pool,
            """
            SELECT checkpoint_mode, status, metadata_json
            FROM graph_checkpoint_runs
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (conversation_id,),
        )
        assert checkpoint_run_rows
        assert any(row["checkpoint_mode"] == "mysql" and row["status"] == "SUCCEEDED" for row in checkpoint_run_rows)
        assert checkpoint_run_rows[-1]["metadata_json"]["checkpoint_mode"] == "mysql"
        assert checkpoint_run_rows[-1]["metadata_json"]["config_summary"]["thread_id"] == conversation_id
        assert "response_text" not in checkpoint_run_rows[-1]["metadata_json"]

        reopened = build_checkpointer("mysql", settings=settings)
        reopened_graph = build_workflow_graph(checkpointer=reopened.checkpointer)
        snapshot = reopened_graph.get_state({"configurable": {"thread_id": conversation_id}})

        assert snapshot.values["conversation_id"] == conversation_id
        assert snapshot.values["chat_id"] == chat_id
        assert snapshot.values["raw_user_input"] == ""
        assert snapshot.values["route"] == "clarification"
        assert snapshot.values["response_text"] == outbound_rows[0]["payload_json"]["text"]

        second_batch_result = await process_next_batch(
            pool,
            limit=1,
            checkpoint_mode="mysql",
            settings=settings,
        )

        assert second_batch_result["processed"] == 0
        assert second_batch_result["failed"] == 0
        assert second_batch_result["enqueued"] == 0

        outbound_rows_after_repeat = await fetch_all(
            pool,
            "SELECT id FROM outbound_messages WHERE conversation_id = %s ORDER BY id",
            (conversation_id,),
        )
        assert len(outbound_rows_after_repeat) == 1
    finally:
        if setup_managed is not None:
            setup_managed.close()
        if reopened is not None:
            reopened.close()
        await cleanup_gateway_mysql_smoke(pool, test_id)
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


async def fetch_one(pool, sql: str, args: tuple) -> dict:
    rows = await fetch_all(pool, sql, args)
    assert len(rows) == 1
    return rows[0]


async def fetch_all(pool, sql: str, args: tuple) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            rows = list(await cur.fetchall())
    for row in rows:
        if "payload_json" in row and isinstance(row["payload_json"], str):
            row["payload_json"] = json.loads(row["payload_json"])
        if "metadata_json" in row and isinstance(row["metadata_json"], str):
            row["metadata_json"] = json.loads(row["metadata_json"])
    return rows


async def cleanup_gateway_mysql_smoke(pool, test_id: str) -> None:
    await assert_mysql_test_database(pool)
    chat_like = f"{test_id}%"
    conversation_like = f"livechat:{test_id}%"
    dedup_like = f"integration:{test_id}%"
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM graph_checkpoint_runs WHERE conversation_id LIKE %s", (conversation_like,))
            await cur.execute("DELETE FROM conversation_messages WHERE conversation_id LIKE %s", (conversation_like,))
            await cur.execute("DELETE FROM external_commands WHERE conversation_id LIKE %s", (conversation_like,))
            await cur.execute("DELETE FROM outbound_messages WHERE conversation_id LIKE %s", (conversation_like,))
            await cur.execute("DELETE FROM conversation_states WHERE chat_id LIKE %s", (chat_like,))
            await cur.execute("DELETE FROM inbound_events WHERE dedup_key LIKE %s", (dedup_like,))
