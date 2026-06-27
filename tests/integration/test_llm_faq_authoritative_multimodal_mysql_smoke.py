import json
import uuid

import aiomysql
import pytest

from app.db.repositories import InboundEventRepository, KnowledgeDocumentRepository
from app.schemas.events import InboundEvent
from app.workers import gateway_consumer
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


def test_llm_faq_authoritative_multimodal_mysql_smoke(monkeypatch):
    mysql_test_config()
    run(_test_llm_faq_authoritative_multimodal_mysql_smoke(monkeypatch))


async def _test_llm_faq_authoritative_multimodal_mysql_smoke(monkeypatch) -> None:
    test_id = f"p8b-faq-{uuid.uuid4().hex}"
    chat_id = f"{test_id}:chat"
    thread_id = f"{test_id}:thread"
    conversation_id = f"livechat:{chat_id}"
    settings = await provision_mysql_test_settings(
        llm_provider="mock",
        llm_router_mode="faq_authoritative",
        llm_router_min_confidence=0.75,
        llm_rewrite_shadow_enabled=False,
        llm_intent_shadow_enabled=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    provider = RecordingFaqRouterProvider()

    def fake_build_llm_provider(mode: str, settings=None):
        assert mode == "mock"
        return provider

    monkeypatch.setattr(gateway_consumer, "build_llm_provider", fake_build_llm_provider)

    try:
        await assert_mysql_test_database(pool)
        await KnowledgeDocumentRepository(pool).insert_idempotent(
            {
                "tenant_id": "default",
                "kb_scope": "default",
                "title": f"{test_id} 充值多图文说明",
                "content": "请按以下步骤操作：",
                "keywords": ["怎么存款", "存款", "充值方式"],
                "question_aliases": ["怎么存款", "怎么存款？"],
                "answer_blocks": [
                    {"type": "text", "text": "请按以下步骤操作："},
                    {
                        "type": "image",
                        "asset_key": "deposit_step_1",
                        "platform_asset_map": {"JUE999": "https://cdn.example/deposit_step_1.png"},
                        "caption": "第一步：进入充值页面",
                        "position": "after",
                    },
                    {"type": "text", "text": "选择可用通道后提交。"},
                    {"type": "buttons", "menu_key": "deposit_menu"},
                ],
                "metadata_json": {"intent_id": "deposit_howto", "test_id": test_id},
                "language": "zh",
                "priority": 1,
                "enabled": True,
            }
        )
        event = InboundEvent(
            source="integration_test",
            raw_action="integration.llm_faq_authoritative.message",
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
        assert await InboundEventRepository(pool).insert(event) == {"inserted": True, "duplicate": False}

        gateway_result = await gateway_consumer.process_next_batch(
            pool,
            limit=1,
            checkpoint_mode="off",
            settings=settings,
        )

        assert gateway_result["processed"] == 1
        assert gateway_result["failed"] == 0
        assert gateway_result["llm"]["router_mode"] == "faq_authoritative"
        assert len(provider.calls) == 1
        assert provider.calls[0]["deterministic_route"] is None

        outbound_rows = await fetch_all(
            pool,
            """
            SELECT id, inbound_event_id, conversation_id, action_type, command_type,
                   message_type, message_kind, block_index, dedup_key, status, payload_json
            FROM outbound_messages
            WHERE conversation_id = %s
            ORDER BY block_index ASC
            """,
            (conversation_id,),
        )
        assert [row["block_index"] for row in outbound_rows] == [0, 1, 2, 3]
        assert [row["message_type"] for row in outbound_rows] == ["text", "image", "text", "buttons"]
        assert [row["command_type"] for row in outbound_rows] == [
            "livechat.send_text",
            "livechat.send_image",
            "livechat.send_text",
            "livechat.buttons_preview",
        ]
        assert len({row["dedup_key"] for row in outbound_rows}) == 4
        assert outbound_rows[1]["payload_json"] == {
            "asset_key": "deposit_step_1",
            "asset_ref": "https://cdn.example/deposit_step_1.png",
            "caption": "第一步：进入充值页面",
            "position": "after",
        }
        assert outbound_rows[3]["payload_json"] == {"menu_key": "deposit_menu"}

        sender_client = FakeSenderClient()
        sender_result = await process_sender_batch(pool, sender_client, limit=10)

        assert [result["status"] for result in sender_result] == [
            "SENT",
            "SENT",
            "SENT",
            "SKIPPED_PREVIEW",
        ]
        assert sender_result[1]["delivery_mode"] == "mvp_text_fallback"
        assert sender_client.sent == [
            {"chat_id": chat_id, "thread_id": thread_id, "text": "请按以下步骤操作："},
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "text": "图片：https://cdn.example/deposit_step_1.png\n第一步：进入充值页面",
            },
            {"chat_id": chat_id, "thread_id": thread_id, "text": "选择可用通道后提交。"},
        ]

        status_rows = await fetch_all(
            pool,
            """
            SELECT message_type, status, last_error
            FROM outbound_messages
            WHERE conversation_id = %s
            ORDER BY block_index ASC
            """,
            (conversation_id,),
        )
        assert [row["status"] for row in status_rows] == ["SENT", "SENT", "SENT", "SKIPPED_PREVIEW"]
        assert status_rows[3]["last_error"] == "buttons preview is not sent by sender_worker"

        router_metadata = await fetch_router_metadata(pool, conversation_id)
        assert router_metadata["status"] == "accepted"
        assert router_metadata["route"] == "faq"
        assert router_metadata["final_route"] == "faq"
        assert router_metadata["route_source"] == "llm_faq_authoritative"

        error_rows = await fetch_all(
            pool,
            "SELECT id FROM graph_run_errors WHERE conversation_id = %s",
            (conversation_id,),
        )
        assert error_rows == []
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


class RecordingFaqRouterProvider:
    def __init__(self) -> None:
        self.calls = []

    async def route(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {
            "rewritten_question": "怎么存款？",
            "normalized_query": "怎么存款",
            "language": "zh",
            "intent": "deposit_howto",
            "route": "faq",
            "confidence": 0.95,
            "sop_name": None,
            "faq_query": "怎么存款",
            "risk_level": None,
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "FAQ authoritative smoke route",
            "provider": "mock",
            "mode": "faq_authoritative",
        }


class FakeSenderClient:
    def __init__(self) -> None:
        self.sent = []

    async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
        self.sent.append({"chat_id": chat_id, "thread_id": thread_id, "text": text})
        return {"event_id": f"fake-event-{len(self.sent)}"}


async def fetch_router_metadata(pool, conversation_id: str) -> dict:
    row = await fetch_one(
        pool,
        "SELECT status, metadata_json FROM graph_checkpoint_runs WHERE conversation_id = %s",
        (conversation_id,),
    )
    assert row["status"] == "SUCCEEDED"
    return row["metadata_json"]["llm_router"]


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
