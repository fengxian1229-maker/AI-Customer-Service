import json
from pathlib import Path
from uuid import uuid4

import aiomysql
import pytest

from app.db.repositories import (
    ConversationRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    GatewayTransactionRepository,
    InboundEventRepository,
    OutboundMessageRepository,
)
from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService
from app.workflows.backend_dispute_escalation import backend_conclusion_record
from app.workers.external_result_consumer import process_pending_results

from conftest import (
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql, pytest.mark.replay]
FIXTURE = Path(__file__).parents[1] / "fixtures" / "replay" / "livechat_repeated_backend_dispute_es.json"


def test_repeated_backend_dispute_creates_ack_and_handoff_atomically():
    mysql_test_config()
    run(_test_repeated_backend_dispute_creates_ack_and_handoff_atomically())


async def _test_repeated_backend_dispute_creates_ack_and_handoff_atomically():
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    settings = await provision_mysql_test_settings()
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    test_id = uuid4().hex
    chat_id = f"handoff-replay-{test_id}"
    thread_id = f"thread-{test_id}"
    conversation_repository = ConversationRepository(pool)
    inbound_repository = InboundEventRepository(pool)
    try:
        conversation = await conversation_repository.get_or_create(chat_id=chat_id, thread_id=thread_id)
        conclusion = backend_conclusion_record(case["backend_result"], recorded_at="2026-07-15T00:00:00+00:00")
        applied = await conversation_repository.update_workflow_state(
            conversation["conversation_id"],
            {
                "status": "AI_ACTIVE",
                "active_workflow": "withdrawal_blocked_or_rollover",
                "workflow_stage": "backend_resolved",
                "slot_memory": {
                    "account_or_phone": case["identity"],
                    "identity_source": case["backend_result"]["identity_source"],
                    "backend_conclusion": conclusion,
                    "backend_dispute_count": 0,
                },
            },
        )
        assert applied is True

        service = GatewayService(transactional_repository=GatewayTransactionRepository(pool))
        for index, text in enumerate(case["messages"][2:], start=1):
            event = _event(chat_id, thread_id, test_id, index, text)
            inserted = await inbound_repository.insert(event)
            assert inserted["inserted"] is True
            inbound_event_id = await _inbound_id(pool, event.dedup_key)
            result = await service.process_event(inbound_event_id, event)
            if index == 1:
                assert result["graph_state"]["status"] != "HANDOFF_REQUESTED"

        state = await conversation_repository.get_by_conversation_id(conversation["conversation_id"])
        assert state["status"] != "HANDOFF_REQUESTED"
        ack_count, command_count = await _handoff_counts(pool, conversation["conversation_id"])
        assert (ack_count, command_count) == (0, 0)

        backend_command = await _latest_backend_command(pool, conversation["conversation_id"])
        result_repository = ExternalCommandResultRepository(pool)
        await result_repository.insert_idempotent(
            {
                "external_command_id": backend_command["id"],
                "tenant_id": "default",
                "conversation_id": conversation["conversation_id"],
                "chat_id": chat_id,
                "thread_id": thread_id,
                "inbound_event_id": backend_command["inbound_event_id"],
                "command_type": "backend.query",
                "result_type": "backend.query.result",
                "result_json": {
                    **case["backend_result"],
                    "reply_language": "es",
                    "query": {"player_found": True, "remaining_turnover": 18.88, "is_met": False},
                },
                "status": "PENDING",
            }
        )
        await process_pending_results(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=OutboundMessageRepository(pool),
            transaction_repository=ExternalResultTransactionRepository(pool),
            worker_id=f"handoff-replay-{test_id}",
        )

        state = await conversation_repository.get_by_conversation_id(conversation["conversation_id"])
        assert state["status"] == case["expected_status"]
        assert state["active_workflow"] == "human_handoff"

        ack_count, command_count = await _handoff_counts(pool, conversation["conversation_id"])
        assert ack_count == case["expected_handoff_acks"]
        assert command_count == case["expected_handoff_commands"]
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


def _event(chat_id: str, thread_id: str, test_id: str, index: int, text: str) -> InboundEvent:
    event_id = f"handoff-replay-{test_id}-{index}"
    return InboundEvent(
        source="db_replay",
        raw_action="db_replay.message",
        chat_id=chat_id,
        thread_id=thread_id,
        event_id=event_id,
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="customer",
        sender_role="external",
        occurred_at=f"2026-07-15 00:00:0{index}.000000",
        dedup_key=f"db_replay:{event_id}",
        payload_json={"event": {"text": text}, "text": text},
        ignored=False,
    )


async def _handoff_counts(pool, conversation_id: str) -> tuple[int, int]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM outbound_messages
                WHERE conversation_id = %s
                  AND JSON_EXTRACT(payload_json, '$.handoff_ack') = TRUE
                """,
                (conversation_id,),
            )
            ack_count = int((await cur.fetchone())["count"])
            await cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM external_commands
                WHERE conversation_id = %s
                  AND command_type = 'human_handoff.requested'
                """,
                (conversation_id,),
            )
            command_count = int((await cur.fetchone())["count"])
    return ack_count, command_count


async def _inbound_id(pool, dedup_key: str) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM inbound_events WHERE dedup_key = %s", (dedup_key,))
            row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _latest_backend_command(pool, conversation_id: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT id, inbound_event_id
                FROM external_commands
                WHERE conversation_id = %s AND command_type = 'backend.query'
                ORDER BY id DESC
                LIMIT 1
                """,
                (conversation_id,),
            )
            row = await cur.fetchone()
    assert row is not None
    return row
