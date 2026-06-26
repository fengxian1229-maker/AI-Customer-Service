import uuid
import json

import aiomysql
import pytest

from app.db.repositories import (
    ConversationRepository,
    ExternalCommandRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    GatewayTransactionRepository,
    InboundEventRepository,
    OutboundMessageRepository,
)
from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService
from app.workers.external_command_worker import process_pending_commands
from app.workers.external_result_consumer import process_pending_results

from conftest import (
    assert_mysql_test_database,
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql, pytest.mark.replay]


def test_db_replay_runner_mysql_mock_closed_loop():
    mysql_test_config()
    run(_test_db_replay_runner_mysql_mock_closed_loop())


async def _test_db_replay_runner_mysql_mock_closed_loop():
    test_id = f"db-replay-{uuid.uuid4().hex}"
    settings = await provision_mysql_test_settings()
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        cases = [
            {
                "name": "deposit_missing_incomplete",
                "text": "我的存款还没到账",
                "expected_workflow_stage": "collecting_slots",
                "expected_external_command_types": [],
                "run_mock_result": False,
            },
            {
                "name": "deposit_missing_complete",
                "text": "我的存款订单 D123456 没到账，金额 1000，渠道 GCASH",
                "expected_workflow_stage": "waiting_backend",
                "expected_external_command_types": ["telegram.send_case_card"],
                "expected_command_slots": {
                    "deposit_order_id": "D123456",
                    "amount": "1000",
                    "channel": "GCASH",
                },
                "run_mock_result": True,
                "expected_result_stage": "case_created",
            },
            {
                "name": "withdrawal_missing_incomplete",
                "text": "我的提款一直没到账",
                "expected_workflow_stage": "collecting_slots",
                "expected_external_command_types": [],
                "run_mock_result": False,
            },
            {
                "name": "withdrawal_missing_complete",
                "text": "我的提款订单 W987654 没到账，金额 500，渠道 银行卡",
                "expected_workflow_stage": "waiting_backend",
                "expected_external_command_types": ["telegram.send_case_card"],
                "expected_command_slots": {
                    "withdrawal_order_id": "W987654",
                    "amount": "500",
                    "channel": "银行卡",
                },
                "run_mock_result": True,
                "expected_result_stage": "case_created",
            },
            {
                "name": "rag_direct_reply",
                "text": "que promociones tienen hoy",
                "expected_workflow_stage": None,
                "expected_external_command_types": [],
                "run_mock_result": False,
            },
            {
                "name": "human_handoff",
                "text": "quiero hablar con un agente humano",
                "expected_workflow_stage": "handoff_requested",
                "expected_external_command_types": ["human_handoff.requested"],
                "run_mock_result": True,
                "expected_result_stage": "handoff_requested",
            },
        ]

        for case in cases:
            await run_db_replay_case(pool, test_id, case)
    finally:
        await cleanup_db_replay(pool, test_id)
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)


async def run_db_replay_case(pool, test_id: str, case: dict) -> None:
    event = make_replay_event(test_id, case)
    inbound_repository = InboundEventRepository(pool)
    insert_result = await inbound_repository.insert(event)
    assert insert_result["inserted"] is True
    inbound_event_id = await fetch_inbound_event_id(pool, event.dedup_key)

    service = GatewayService(
        transactional_repository=GatewayTransactionRepository(pool),
    )
    first_result = await service.process_event(inbound_event_id, event)
    second_result = await service.process_event(inbound_event_id, event)

    assert first_result["graph_state"]["workflow_stage"] == case["expected_workflow_stage"]
    assert second_result["graph_state"]["workflow_stage"] == case["expected_workflow_stage"]

    conversation = await fetch_conversation(pool, event.chat_id)
    assert conversation["conversation_id"] == f"livechat:{event.chat_id}"
    assert conversation["workflow_stage"] == case["expected_workflow_stage"]

    outbound_rows = await fetch_outbound_rows(pool, conversation["conversation_id"])
    assert [row["action_type"] for row in outbound_rows].count("send_event") == 1

    command_rows = await fetch_external_command_rows(pool, conversation["conversation_id"])
    assert [row["command_type"] for row in command_rows] == case["expected_external_command_types"]
    assert len({row["dedup_key"] for row in command_rows}) == len(command_rows)
    if case.get("expected_command_slots"):
        payload = command_rows[0]["payload_json"]
        slot_memory = payload["slot_memory"]
        for key, expected in case["expected_command_slots"].items():
            assert slot_memory[key] == expected

    duplicate_insert = await inbound_repository.insert(event)
    assert duplicate_insert["duplicate"] is True
    assert len(await fetch_external_command_rows(pool, conversation["conversation_id"])) == len(command_rows)
    assert len(await fetch_outbound_rows(pool, conversation["conversation_id"])) == len(outbound_rows)

    if not case["run_mock_result"]:
        return

    command_repository = ExternalCommandRepository(pool)
    result_repository = ExternalCommandResultRepository(pool)
    worker_results = await process_pending_commands(
        command_repository,
        result_repository=result_repository,
        limit=20,
        dry_run=True,
        emit_result=True,
        worker_id=f"{test_id}:command-worker",
    )
    assert len(worker_results) == len(command_rows)

    result_rows = await fetch_external_command_result_rows(pool, conversation["conversation_id"])
    assert len(result_rows) == len(command_rows)
    assert {row["status"] for row in result_rows} == {"PENDING"}

    consumer_results = await process_pending_results(
        result_repository=result_repository,
        conversation_repository=ConversationRepository(pool),
        outbound_repository=OutboundMessageRepository(pool),
        transaction_repository=ExternalResultTransactionRepository(pool),
        limit=20,
        worker_id=f"{test_id}:result-consumer",
    )
    assert len(consumer_results) == len(result_rows)
    assert {row["status"] for row in await fetch_external_command_result_rows(pool, conversation["conversation_id"])} == {
        "PROCESSED"
    }

    updated_conversation = await fetch_conversation(pool, event.chat_id)
    assert updated_conversation["workflow_stage"] == case["expected_result_stage"]

    processed_again = await process_pending_results(
        result_repository=result_repository,
        conversation_repository=ConversationRepository(pool),
        outbound_repository=OutboundMessageRepository(pool),
        transaction_repository=ExternalResultTransactionRepository(pool),
        limit=20,
        worker_id=f"{test_id}:result-consumer-repeat",
    )
    assert processed_again == []


def make_replay_event(test_id: str, case: dict) -> InboundEvent:
    chat_id = f"{test_id}:{case['name']}:chat"
    event_id = f"{test_id}:{case['name']}:event"
    return InboundEvent(
        source="db_replay",
        raw_action="db_replay.message",
        chat_id=chat_id,
        thread_id=f"{test_id}:{case['name']}:thread",
        event_id=event_id,
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="customer",
        sender_role="external",
        occurred_at="2026-06-25 00:00:00.000000",
        dedup_key=f"db_replay:{event_id}",
        payload_json={"event": {"text": case["text"]}, "text": case["text"]},
        ignored=False,
    )


async def fetch_inbound_event_id(pool, dedup_key: str) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM inbound_events WHERE dedup_key = %s", (dedup_key,))
            row = await cur.fetchone()
    return row[0]


async def fetch_conversation(pool, chat_id: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT conversation_id, chat_id, active_workflow, workflow_stage FROM conversation_states WHERE chat_id = %s",
                (chat_id,),
            )
            return await cur.fetchone()


async def fetch_outbound_rows(pool, conversation_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, action_type, status FROM outbound_messages WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            )
            return list(await cur.fetchall())


async def fetch_external_command_rows(pool, conversation_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, command_type, status, dedup_key, payload_json FROM external_commands WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            )
            rows = list(await cur.fetchall())
    for row in rows:
        if isinstance(row["payload_json"], str):
            row["payload_json"] = json.loads(row["payload_json"])
    return rows


async def fetch_external_command_result_rows(pool, conversation_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, result_type, status FROM external_command_results WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            )
            return list(await cur.fetchall())


async def cleanup_db_replay(pool, test_id: str) -> None:
    await assert_mysql_test_database(pool)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM external_command_results WHERE chat_id LIKE %s", (f"{test_id}:%",))
            await cur.execute("DELETE FROM external_commands WHERE chat_id LIKE %s", (f"{test_id}:%",))
            await cur.execute("DELETE FROM outbound_messages WHERE chat_id LIKE %s", (f"{test_id}:%",))
            await cur.execute("DELETE FROM conversation_states WHERE chat_id LIKE %s", (f"{test_id}:%",))
            await cur.execute("DELETE FROM inbound_events WHERE dedup_key LIKE %s", (f"db_replay:{test_id}:%",))
