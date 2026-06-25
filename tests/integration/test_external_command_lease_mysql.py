import asyncio
import uuid

from app.db.repositories import ExternalCommandRepository

import pytest

from conftest import assert_mysql_test_database, create_bootstrapped_mysql_pool, mysql_test_config, run


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_external_command_concurrent_lease_mysql():
    mysql_test_config()
    run(_test_external_command_concurrent_lease_mysql())


async def _test_external_command_concurrent_lease_mysql():
    test_id = f"lease-cmd-{uuid.uuid4().hex}"
    pool = await create_bootstrapped_mysql_pool()
    try:
        repository = ExternalCommandRepository(pool)
        inserted_ids = []
        for index in range(8):
            result = await repository.insert_idempotent(
                {
                    "tenant_id": test_id,
                    "conversation_id": f"{test_id}:conversation:{index}",
                    "chat_id": f"{test_id}:chat:{index}",
                    "thread_id": f"{test_id}:thread:{index}",
                    "inbound_event_id": index + 1,
                    "command_type": "backend.query",
                    "payload_json": {"index": index},
                }
            )
            inserted_ids.append(result["id"])

        worker_ids = [f"{test_id}:worker:{index}" for index in range(4)]
        leased_batches = await asyncio.gather(
            *[_lease_commands_in_independent_pool(worker_id, limit=3, lease_seconds=120) for worker_id in worker_ids]
        )
        leased = [row for batch in leased_batches for row in batch]
        leased_ids = [row["id"] for row in leased]

        assert len(leased_ids) == len(set(leased_ids))
        assert len(leased_ids) <= len(inserted_ids)
        assert set(leased_ids) <= set(inserted_ids)
        for row in leased:
            assert row["locked_by"] in worker_ids
            assert row["leased_at"] is not None
            assert row["lease_expires_at"] is not None
            assert row["status"] in {"PENDING", "RETRYABLE"}

        second_worker_rows = await repository.lease_pending(limit=20, worker_id=f"{test_id}:second-pass", lease_seconds=120)
        assert not ({row["id"] for row in second_worker_rows} & set(leased_ids))

        expired_ids = leased_ids[:2]
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(expired_ids))
                await cur.execute(
                    f"UPDATE external_commands SET lease_expires_at = TIMESTAMPADD(SECOND, -1, NOW(6)) WHERE id IN ({placeholders})",
                    tuple(expired_ids),
                )

        recovered = await repository.recover_expired_leases()
        assert recovered >= len(expired_ids)

        released_rows = await repository.lease_pending(limit=20, worker_id=f"{test_id}:recovered-worker", lease_seconds=120)
        assert set(expired_ids) <= {row["id"] for row in released_rows}
    finally:
        await cleanup_external_commands(pool, test_id)
        pool.close()
        await pool.wait_closed()


async def _lease_commands_in_independent_pool(worker_id: str, limit: int, lease_seconds: int):
    pool = await create_bootstrapped_mysql_pool()
    try:
        return await ExternalCommandRepository(pool).lease_pending(
            limit=limit,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def cleanup_external_commands(pool, test_id: str) -> None:
    await assert_mysql_test_database(pool)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM external_commands WHERE tenant_id = %s", (test_id,))
