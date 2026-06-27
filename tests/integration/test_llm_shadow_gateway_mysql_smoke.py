import json
import uuid

import aiomysql
import pytest

from app.db.repositories import InboundEventRepository, KnowledgeDocumentRepository
from app.schemas.events import InboundEvent
from app.workers import gateway_consumer

from conftest import (
    assert_mysql_test_database,
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_llm_shadow_gateway_mysql_smoke_records_shadow_without_changing_outbound():
    mysql_test_config()
    run(_test_llm_shadow_gateway_mysql_smoke_records_shadow_without_changing_outbound())


async def _test_llm_shadow_gateway_mysql_smoke_records_shadow_without_changing_outbound() -> None:
    test_id = f"p7a8-shadow-{uuid.uuid4().hex}"
    settings = await provision_mysql_test_settings(
        llm_provider="mock",
        llm_rewrite_shadow_enabled=True,
        llm_intent_shadow_enabled=True,
        llm_rewrite_fallback_enabled=False,
        llm_intent_fallback_enabled=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        await assert_mysql_test_database(pool)
        event = await seed_faq_event(pool, test_id)
        batch_result = await gateway_consumer.process_next_batch(
            pool,
            limit=1,
            checkpoint_mode="off",
            settings=settings,
        )

        assert batch_result["processed"] == 1
        assert batch_result["failed"] == 0
        assert batch_result["enqueued"] == 1
        assert batch_result["llm"]["provider"] == "mock"
        assert batch_result["llm"]["rewrite_shadow_enabled"] is True
        assert batch_result["llm"]["intent_shadow_enabled"] is True
        assert batch_result["llm"]["shadow_active"] is True
        assert batch_result["llm"]["fallback_enabled"] is False

        conversation_id = f"livechat:{event.chat_id}"
        outbound = await fetch_one(
            pool,
            """
            SELECT message_type, status, payload_json
            FROM outbound_messages
            WHERE conversation_id = %s
            """,
            (conversation_id,),
        )
        assert outbound["message_type"] == "text"
        assert outbound["status"] == "PENDING"
        assert outbound["payload_json"]["text"] == "可以在充值页面选择可用通道，并按页面提示完成存款。"

        messages = await fetch_all(
            pool,
            "SELECT sender_role FROM conversation_messages WHERE conversation_id = %s ORDER BY id",
            (conversation_id,),
        )
        assert [row["sender_role"] for row in messages] == ["customer"]

        checkpoint = await fetch_one(
            pool,
            """
            SELECT status, metadata_json
            FROM graph_checkpoint_runs
            WHERE conversation_id = %s
            """,
            (conversation_id,),
        )
        assert checkpoint["status"] == "SUCCEEDED"
        shadow = checkpoint["metadata_json"]["llm_shadow"]
        assert shadow["rewrite"]["provider"] == "mock"
        assert shadow["rewrite"]["status"] == "ok"
        assert shadow["intent"]["provider"] == "mock"
        assert shadow["intent"]["status"] == "ok"
        assert shadow["deterministic_route"] == "faq"

        errors = await fetch_all(pool, "SELECT id FROM graph_run_errors WHERE conversation_id = %s", (conversation_id,))
        assert errors == []
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


def test_llm_shadow_gateway_mysql_smoke_records_shadow_error_without_graph_error(monkeypatch):
    mysql_test_config()
    run(_test_llm_shadow_gateway_mysql_smoke_records_shadow_error_without_graph_error(monkeypatch))


async def _test_llm_shadow_gateway_mysql_smoke_records_shadow_error_without_graph_error(monkeypatch) -> None:
    test_id = f"p7a8-shadow-error-{uuid.uuid4().hex}"
    settings = await provision_mysql_test_settings(
        llm_provider="mock",
        llm_rewrite_shadow_enabled=True,
        llm_intent_shadow_enabled=True,
        llm_rewrite_fallback_enabled=False,
        llm_intent_fallback_enabled=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        await assert_mysql_test_database(pool)
        event = await seed_faq_event(pool, test_id)

        def fake_build_llm_provider(mode: str, settings=None):
            return ExplodingShadowProvider()

        monkeypatch.setattr(gateway_consumer, "build_llm_provider", fake_build_llm_provider)

        batch_result = await gateway_consumer.process_next_batch(
            pool,
            limit=1,
            checkpoint_mode="off",
            settings=settings,
        )

        assert batch_result["processed"] == 1
        assert batch_result["failed"] == 0
        assert batch_result["enqueued"] == 1

        conversation_id = f"livechat:{event.chat_id}"
        outbound = await fetch_one(
            pool,
            "SELECT message_type, status, payload_json FROM outbound_messages WHERE conversation_id = %s",
            (conversation_id,),
        )
        assert outbound["message_type"] == "text"
        assert outbound["status"] == "PENDING"
        assert outbound["payload_json"]["text"] == "可以在充值页面选择可用通道，并按页面提示完成存款。"

        checkpoint = await fetch_one(
            pool,
            "SELECT status, metadata_json FROM graph_checkpoint_runs WHERE conversation_id = %s",
            (conversation_id,),
        )
        assert checkpoint["status"] == "SUCCEEDED"
        shadow = checkpoint["metadata_json"]["llm_shadow"]
        assert shadow["rewrite"] == {"mode": "shadow", "status": "error", "error_type": "RuntimeError"}
        assert shadow["intent"] == {"mode": "shadow", "status": "error", "error_type": "RuntimeError"}
        assert "api_key" not in str(shadow)
        assert "password" not in str(shadow)

        errors = await fetch_all(pool, "SELECT id FROM graph_run_errors WHERE conversation_id = %s", (conversation_id,))
        assert errors == []
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


class ExplodingShadowProvider:
    async def rewrite(self, payload: dict) -> dict:
        raise RuntimeError("shadow rewrite failed api_key=hidden")

    async def classify_intent(self, payload: dict) -> dict:
        raise RuntimeError("shadow intent failed password=hidden")


async def seed_faq_event(pool, test_id: str) -> InboundEvent:
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
        raw_action="integration.llm_shadow.message",
        chat_id=f"{test_id}:chat",
        thread_id=f"{test_id}:thread",
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
    return event


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
