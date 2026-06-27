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


def test_llm_guarded_authoritative_router_mysql_smoke(monkeypatch):
    mysql_test_config()
    run(_test_llm_guarded_authoritative_router_mysql_smoke(monkeypatch))


async def _test_llm_guarded_authoritative_router_mysql_smoke(monkeypatch) -> None:
    test_id = f"p8a-router-{uuid.uuid4().hex}"
    settings = await provision_mysql_test_settings(
        llm_provider="mock",
        llm_router_mode="guarded_authoritative",
        llm_router_min_confidence=0.75,
        llm_router_fallback_to_deterministic=True,
        llm_rewrite_shadow_enabled=False,
        llm_intent_shadow_enabled=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    provider = RecordingRouterProvider()

    def fake_build_llm_provider(mode: str, settings=None):
        assert mode == "mock"
        return provider

    monkeypatch.setattr(gateway_consumer, "build_llm_provider", fake_build_llm_provider)

    try:
        await assert_mysql_test_database(pool)
        await seed_faq_document(pool, test_id)
        faq_event = await seed_event(pool, test_id, "faq", "怎么存款？")
        sop_event = await seed_event(pool, test_id, "sop", "存款订单 D123456 没到账")
        low_confidence_event = await seed_event(pool, test_id, "low-confidence", "how to deposit low confidence")
        active_event = await seed_active_workflow_event(pool, test_id)

        batch_result = await gateway_consumer.process_next_batch(
            pool,
            limit=4,
            checkpoint_mode="off",
            settings=settings,
        )

        assert batch_result["processed"] == 4
        assert batch_result["failed"] == 0
        assert batch_result["llm"]["router_mode"] == "guarded_authoritative"
        assert batch_result["llm"]["router_min_confidence"] == 0.75
        assert batch_result["llm"]["router_fallback_to_deterministic"] is True
        assert len(provider.calls) == 3
        assert f"livechat:{active_event.chat_id}" not in [call["conversation_id"] for call in provider.calls]

        faq_conversation_id = f"livechat:{faq_event.chat_id}"
        faq_outbound = await fetch_one(
            pool,
            "SELECT message_type, status, payload_json FROM outbound_messages WHERE conversation_id = %s",
            (faq_conversation_id,),
        )
        assert faq_outbound["message_type"] == "text"
        assert faq_outbound["status"] == "PENDING"
        assert faq_outbound["payload_json"]["text"] == "可以在充值页面选择可用通道，并按页面提示完成存款。"
        faq_router = await fetch_router_metadata(pool, faq_conversation_id)
        assert faq_router["status"] == "accepted"
        assert faq_router["route"] == "faq"
        assert faq_router["final_route"] == "faq"
        assert faq_router["route_source"] == "llm_guarded_authoritative"

        sop_router = await fetch_router_metadata(pool, f"livechat:{sop_event.chat_id}")
        assert sop_router["status"] == "accepted"
        assert sop_router["route"] == "sop"
        assert sop_router["final_route"] == "sop"
        assert sop_router["final_intent"] == "deposit_missing"
        sop_outbound = await fetch_one(
            pool,
            "SELECT payload_json FROM outbound_messages WHERE conversation_id = %s",
            (f"livechat:{sop_event.chat_id}",),
        )
        assert sop_outbound["payload_json"]["text"] != "可以在充值页面选择可用通道，并按页面提示完成存款。"

        low_confidence_router = await fetch_router_metadata(pool, f"livechat:{low_confidence_event.chat_id}")
        assert low_confidence_router["status"] == "fallback"
        assert low_confidence_router["fallback_reason"] == "low_confidence"
        assert low_confidence_router["route_source"] == "deterministic"

        active_router = await fetch_router_metadata(pool, f"livechat:{active_event.chat_id}")
        assert active_router["status"] == "fallback"
        assert active_router["fallback_reason"] == "hard_guard"
        assert active_router["hard_guard"] == "active_workflow"
        assert active_router["final_route"] == "sop"

        for event in [faq_event, sop_event, low_confidence_event, active_event]:
            errors = await fetch_all(
                pool,
                "SELECT id FROM graph_run_errors WHERE conversation_id = %s",
                (f"livechat:{event.chat_id}",),
            )
            assert errors == []
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


class RecordingRouterProvider:
    def __init__(self) -> None:
        self.calls = []

    async def route(self, payload: dict) -> dict:
        self.calls.append(payload)
        text = payload.get("raw_user_input") or ""
        if "low confidence" in text:
            return {
                **self._faq_result(),
                "confidence": 0.2,
                "reason": "low confidence fallback fixture",
            }
        if "D123456" in text:
            return {
                "rewritten_question": "存款订单 D123456 没到账",
                "normalized_query": "存款订单 D123456 没到账",
                "language": "zh",
                "intent": "deposit_missing",
                "route": "sop",
                "confidence": 0.95,
                "sop_name": "deposit_missing",
                "faq_query": None,
                "risk_level": "elevated",
                "requires_human": False,
                "requires_backend": True,
                "missing_slots": [],
                "preserved_entities": ["D123456"],
                "reason": "deposit missing requires SOP",
                "provider": "mock",
                "mode": "guarded_authoritative",
            }
        return self._faq_result()

    def _faq_result(self) -> dict:
        return {
            "rewritten_question": "怎么存款？",
            "normalized_query": "怎么存款？",
            "language": "zh",
            "intent": "deposit_howto",
            "route": "faq",
            "confidence": 0.95,
            "sop_name": None,
            "faq_query": "怎么存款？",
            "risk_level": None,
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "deposit how-to FAQ",
            "provider": "mock",
            "mode": "guarded_authoritative",
        }


async def seed_faq_document(pool, test_id: str) -> None:
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


async def seed_event(pool, test_id: str, case_id: str, text: str) -> InboundEvent:
    event = InboundEvent(
        source="integration_test",
        raw_action="integration.llm_router.message",
        chat_id=f"{test_id}:{case_id}:chat",
        thread_id=f"{test_id}:{case_id}:thread",
        event_id=f"{test_id}:{case_id}:event",
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="customer",
        sender_role="external",
        occurred_at="2026-06-27 00:00:00.000000",
        dedup_key=f"integration:{test_id}:{case_id}",
        payload_json={"event": {"type": "message", "text": text}, "text": text},
        ignored=False,
    )
    insert_result = await InboundEventRepository(pool).insert(event)
    assert insert_result == {"inserted": True, "duplicate": False}
    return event


async def seed_active_workflow_event(pool, test_id: str) -> InboundEvent:
    chat_id = f"{test_id}:active:chat"
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO conversation_states (
                    conversation_id, tenant_id, channel_type, chat_id, current_thread_id,
                    status, active_workflow, workflow_stage, slot_memory
                )
                VALUES (%s, 'default', 'livechat', %s, %s, 'AI_ACTIVE', 'deposit_missing', 'collecting_slots', JSON_OBJECT())
                """,
                (f"livechat:{chat_id}", chat_id, f"{test_id}:active:thread"),
            )
        await conn.commit()
    return await seed_event(pool, test_id, "active", "怎么存款？")


async def fetch_router_metadata(pool, conversation_id: str) -> dict:
    checkpoint = await fetch_one(
        pool,
        "SELECT status, metadata_json FROM graph_checkpoint_runs WHERE conversation_id = %s",
        (conversation_id,),
    )
    assert checkpoint["status"] == "SUCCEEDED"
    return checkpoint["metadata_json"]["llm_router"]


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
