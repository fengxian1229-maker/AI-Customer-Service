import json
import uuid

import aiomysql
import pytest

from app.db.repositories import (
    FaqSmokeReadRepository,
    InboundEventRepository,
    KnowledgeDocumentRepository,
    OutboundMessageRepository,
)
from app.schemas.events import InboundEvent
from app.workers.gateway_consumer import process_next_batch as process_gateway_batch
from app.workers.sender_worker import process_next_batch as process_sender_batch

from conftest import (
    assert_mysql_test_database,
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_outbound_fetch_pending_status_is_not_ambiguous_with_conversation_status():
    mysql_test_config()
    run(_test_outbound_fetch_pending_status_is_not_ambiguous_with_conversation_status())


async def _test_outbound_fetch_pending_status_is_not_ambiguous_with_conversation_status() -> None:
    test_id = f"p7a7-pending-{uuid.uuid4().hex}"
    chat_id = f"{test_id}:chat"
    conversation_id = f"livechat:{chat_id}"
    settings = await provision_mysql_test_settings()
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        await assert_mysql_test_database(pool)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO conversation_states (conversation_id, chat_id, status)
                    VALUES (%s, %s, 'AI_ACTIVE')
                    """,
                    (conversation_id, chat_id),
                )
                await cur.execute(
                    """
                    INSERT INTO outbound_messages (
                      chat_id, thread_id, action_type, message_type, payload_json,
                      status, inbound_event_id, conversation_id
                    ) VALUES (%s, %s, 'send_event', 'text', CAST(%s AS JSON), 'PENDING', NULL, %s)
                    """,
                    (chat_id, f"{test_id}:thread", json.dumps({"text": "hello"}), conversation_id),
                )

        rows = await OutboundMessageRepository(pool).fetch_pending(limit=5)

        assert len(rows) == 1
        assert rows[0]["conversation_id"] == conversation_id
        assert rows[0]["status"] == "PENDING"
        assert rows[0]["payload_json"]["text"] == "hello"
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


def test_faq_single_text_closed_loop_mysql_smoke_with_fake_sender():
    mysql_test_config()
    run(_test_faq_single_text_closed_loop_mysql_smoke_with_fake_sender())


async def _test_faq_single_text_closed_loop_mysql_smoke_with_fake_sender() -> None:
    test_id = f"p7a7-faq-{uuid.uuid4().hex}"
    chat_id = f"{test_id}:chat"
    thread_id = f"{test_id}:thread"
    conversation_id = f"livechat:{chat_id}"
    settings = await provision_mysql_test_settings(
        llm_provider="off",
        llm_rewrite_shadow_enabled=False,
        llm_intent_shadow_enabled=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        await assert_mysql_test_database(pool)
        assert settings.llm_provider == "off"
        assert settings.llm_rewrite_shadow_enabled is False
        assert settings.llm_intent_shadow_enabled is False

        await KnowledgeDocumentRepository(pool).insert_idempotent(
            {
                "tenant_id": "default",
                "kb_scope": "default",
                "title": f"{test_id} 充值方式说明",
                "content": "可以在充值页面选择可用通道，并按页面提示完成存款。",
                "keywords": ["怎么存款", "存款", "充值方式"],
                "question_aliases": ["怎么存款？", "怎么存款"],
                "answer_blocks": [{"type": "text", "text": "可以在充值页面选择可用通道，并按页面提示完成存款。"}],
                "metadata_json": {"intent_id": "deposit_howto", "test_id": test_id},
                "language": "zh",
                "priority": 1,
                "enabled": True,
            }
        )
        event = InboundEvent(
            source="integration_test",
            raw_action="integration.faq_smoke.message",
            chat_id=chat_id,
            thread_id=thread_id,
            event_id=f"{test_id}:event",
            event_type="message",
            standard_event_type="MESSAGE_CREATED",
            author_id="customer",
            sender_role="external",
            occurred_at="2026-06-27 00:00:00.000000",
            dedup_key=f"integration:{test_id}",
            payload_json={"event": {"type": "message", "text": "怎么存款？"}, "text": "怎么存款？"},
            ignored=False,
        )
        insert_result = await InboundEventRepository(pool).insert(event)
        assert insert_result == {"inserted": True, "duplicate": False}

        gateway_result = await process_gateway_batch(
            pool,
            limit=1,
            checkpoint_mode="off",
            settings=settings,
        )

        assert gateway_result["processed"] == 1
        assert gateway_result["failed"] == 0
        assert gateway_result["enqueued"] == 1
        assert gateway_result["llm"] == {
            "provider": "off",
            "rewrite_shadow_enabled": False,
            "intent_shadow_enabled": False,
            "shadow_active": False,
        }

        inbound_row = await fetch_one(
            pool,
            "SELECT id, processed FROM inbound_events WHERE dedup_key = %s",
            (event.dedup_key,),
        )
        assert inbound_row["processed"] == 1
        inbound_event_id = inbound_row["id"]

        outbound_rows = await fetch_all(
            pool,
            """
            SELECT id, inbound_event_id, conversation_id, action_type, command_type,
                   message_type, message_kind, status, payload_json
            FROM outbound_messages
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (conversation_id,),
        )
        assert len(outbound_rows) == 1
        assert outbound_rows[0]["inbound_event_id"] == inbound_event_id
        assert outbound_rows[0]["message_type"] == "text"
        assert outbound_rows[0]["message_kind"] == "text"
        assert outbound_rows[0]["status"] == "PENDING"
        assert outbound_rows[0]["payload_json"]["text"]

        sender_client = FakeSenderClient()
        sender_result = await process_sender_batch(pool, sender_client, limit=20)

        assert sender_result == [{"status": "SENT", "last_error": None, "retryable": False}]
        assert sender_client.sent == [
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "text": outbound_rows[0]["payload_json"]["text"],
            }
        ]

        sent_outbound = await fetch_one(
            pool,
            """
            SELECT id, status, sent_at, last_error
            FROM outbound_messages
            WHERE id = %s
            """,
            (outbound_rows[0]["id"],),
        )
        assert sent_outbound["status"] == "SENT"
        assert sent_outbound["sent_at"] is not None
        assert sent_outbound["last_error"] is None

        message_rows = await fetch_all(
            pool,
            """
            SELECT inbound_event_id, outbound_message_id, sender_role, message_type, text_content, source
            FROM conversation_messages
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (conversation_id,),
        )
        assert [row["sender_role"] for row in message_rows] == ["customer", "assistant"]
        assert message_rows[0]["inbound_event_id"] == inbound_event_id
        assert message_rows[0]["text_content"] == "怎么存款？"
        assert message_rows[1]["outbound_message_id"] == outbound_rows[0]["id"]
        assert message_rows[1]["text_content"] == outbound_rows[0]["payload_json"]["text"]

        checkpoint_rows = await fetch_all(
            pool,
            """
            SELECT checkpoint_mode, status, inbound_event_id
            FROM graph_checkpoint_runs
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (conversation_id,),
        )
        assert any(
            row["checkpoint_mode"] == "off"
            and row["status"] == "SUCCEEDED"
            and row["inbound_event_id"] == inbound_event_id
            for row in checkpoint_rows
        )

        error_rows = await fetch_all(
            pool,
            "SELECT id FROM graph_run_errors WHERE conversation_id = %s",
            (conversation_id,),
        )
        assert error_rows == []

        smoke_summary = await FaqSmokeReadRepository(pool).summary(conversation_id=conversation_id, limit=20)
        assert smoke_summary["overall"]["ok"] is True
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


class FakeSenderClient:
    def __init__(self) -> None:
        self.sent = []

    async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
        self.sent.append({"chat_id": chat_id, "thread_id": thread_id, "text": text})
        return {"event_id": f"fake-event-{len(self.sent)}"}


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
    return rows
