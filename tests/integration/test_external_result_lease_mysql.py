import asyncio
import uuid

from app.db.repositories import ExternalCommandResultRepository

from conftest import create_bootstrapped_mysql_pool, mysql_test_config, run


def test_external_command_result_concurrent_lease_mysql():
    mysql_test_config()
    run(_test_external_command_result_concurrent_lease_mysql())


async def _test_external_command_result_concurrent_lease_mysql():
    test_id = f"lease-result-{uuid.uuid4().hex}"
    pool = await create_bootstrapped_mysql_pool()
    try:
        repository = ExternalCommandResultRepository(pool)
        inserted_ids = []
        for index in range(8):
            result = await repository.insert_idempotent(
                {
                    "external_command_id": index + 1,
                    "tenant_id": test_id,
                    "conversation_id": f"{test_id}:conversation:{index}",
                    "chat_id": f"{test_id}:chat:{index}",
                    "thread_id": f"{test_id}:thread:{index}",
                    "inbound_event_id": index + 1,
                    "command_type": "backend.query",
                    "result_type": "backend.query.mock_result",
                    "result_json": {"index": index},
                }
            )
            inserted_ids.append(result["id"])

        worker_ids = [f"{test_id}:consumer:{index}" for index in range(4)]
        leased_batches = await asyncio.gather(
            *[_lease_results_in_independent_pool(worker_id, limit=3, lease_seconds=120) for worker_id in worker_ids]
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
            assert row["retry_count"] == 0

        first_failed_id, second_failed_id = leased_ids[:2]
        await repository.mark_processing_failed(first_failed_id, "temporary", max_retries=3)
        await repository.mark_processing_failed(second_failed_id, "final", max_retries=1)

        status_rows = await fetch_result_status_rows(pool, [first_failed_id, second_failed_id])
        assert status_rows[first_failed_id]["retry_count"] == 1
        assert status_rows[first_failed_id]["status"] == "RETRYABLE"
        assert status_rows[second_failed_id]["retry_count"] == 1
        assert status_rows[second_failed_id]["status"] == "FAILED"

        still_leased_ids = leased_ids[2:4]
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(still_leased_ids))
                await cur.execute(
                    f"UPDATE external_command_results SET lease_expires_at = TIMESTAMPADD(SECOND, -1, NOW(6)) WHERE id IN ({placeholders})",
                    tuple(still_leased_ids),
                )

        recovered = await repository.recover_expired_leases()
        assert recovered >= len(still_leased_ids)
        released_rows = await repository.lease_pending(limit=20, worker_id=f"{test_id}:recovered-consumer", lease_seconds=120)
        assert set(still_leased_ids) <= {row["id"] for row in released_rows}
    finally:
        await cleanup_external_command_results(pool, test_id)
        pool.close()
        await pool.wait_closed()


async def _lease_results_in_independent_pool(worker_id: str, limit: int, lease_seconds: int):
    pool = await create_bootstrapped_mysql_pool()
    try:
        return await ExternalCommandResultRepository(pool).lease_pending(
            limit=limit,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def fetch_result_status_rows(pool, result_ids: list[int]) -> dict[int, dict]:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(result_ids))
            await cur.execute(
                f"SELECT id, status, retry_count, last_error FROM external_command_results WHERE id IN ({placeholders})",
                tuple(result_ids),
            )
            rows = await cur.fetchall()
    return {row[0]: {"status": row[1], "retry_count": row[2], "last_error": row[3]} for row in rows}


async def cleanup_external_command_results(pool, test_id: str) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM external_command_results WHERE tenant_id = %s", (test_id,))
