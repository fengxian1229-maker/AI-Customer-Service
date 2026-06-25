import argparse
import asyncio
import json
import logging
import os
import socket
import time

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ConversationRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    OutboundMessageRepository,
)
from app.services.outbox import build_text_outbox


RESULT_HANDLERS = {
    "telegram.send_case_card.mock_result": {
        "text": "资料已收到，我们会继续确认，请稍候。",
        "workflow_stage": "waiting_backend",
    },
    "telegram.append_to_case.mock_result": {
        "text": "补充资料已收到，我们会继续跟进，请稍候。",
        "workflow_stage": "waiting_backend",
    },
    "backend.query.mock_result": {
        "text": "已收到查询请求，当前为 dry-run 模式，未连接真实后台。",
        "workflow_stage": "backend_query_dry_run",
    },
    "pending_reply.lookup.mock_result": {
        "text": "已收到查询请求，当前为 dry-run 模式，未连接真实 pending reply 查询源。",
        "workflow_stage": "pending_reply_lookup_dry_run",
    },
    "human_handoff.requested.mock_result": {
        "text": "已为您转接真人客服，请稍候。",
        "active_workflow": "human_handoff",
        "workflow_stage": "handoff_requested",
        "status": "HANDOFF_REQUESTED",
    },
    "rag.placeholder.mock_result": {
        "text": "当前为 RAG placeholder，尚未接入真实知识库。",
        "workflow_stage": "rag_placeholder_dry_run",
    },
}


logger = logging.getLogger(__name__)


async def process_pending_results(
    result_repository: ExternalCommandResultRepository,
    conversation_repository: ConversationRepository,
    outbound_repository: OutboundMessageRepository,
    limit: int = 20,
    transaction_repository: ExternalResultTransactionRepository | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> list[dict]:
    worker_id = worker_id or default_worker_id()
    rows = await result_repository.lease_pending(limit=limit, worker_id=worker_id, lease_seconds=lease_seconds)
    if transaction_repository is None:
        transaction_repository = ExternalResultTransactionRepository(
            result_repository.pool,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            result_repository=result_repository,
        )
    processed = []
    for row in rows:
        try:
            handler = RESULT_HANDLERS.get(row["result_type"])
            if handler is None:
                raise ValueError(f"unsupported result_type: {row['result_type']}")

            graph_state = {
                "status": handler.get("status") or "WAITING_EXTERNAL",
                "active_workflow": handler.get("active_workflow"),
                "workflow_stage": handler.get("workflow_stage"),
                "slot_memory": {},
            }
            outbound = build_text_outbox(
                chat_id=row["chat_id"],
                thread_id=row.get("thread_id"),
                conversation_id=row["conversation_id"],
                inbound_event_id=row.get("inbound_event_id"),
                text=handler["text"],
            )
            await transaction_repository.process_result_transactionally(
                row,
                graph_state=graph_state,
                outbound_messages=[outbound],
                external_commands=[],
            )
            processed.append({"id": row["id"], "result_type": row["result_type"], "status": "PROCESSED"})
        except Exception as exc:
            await result_repository.mark_processing_failed(row["id"], str(exc), max_retries=max_retries)
            processed.append({"id": row["id"], "result_type": row.get("result_type"), "status": "FAILED", "error": str(exc)})
    return processed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume external_command_results into conversation state and outbox.")
    parser.add_argument("--once", action="store_true", help="Run one external result batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external command results to process.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id used for queue leases.")
    parser.add_argument("--lease-seconds", type=int, default=60, help="Seconds before a queue lease expires.")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum processing attempts before FAILED.")
    parser.add_argument(
        "--recover-interval-seconds",
        type=int,
        default=30,
        help="Seconds between expired lease recovery attempts in long-running mode. Use <= 0 to disable.",
    )
    return parser


def default_worker_id() -> str:
    return f"external-result-consumer-{socket.gethostname()}-{os.getpid()}"


async def run_once(
    limit: int,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-result-consumer",
        livechat_account_id="unused-for-external-result-consumer",
    )
    pool = await create_pool(settings)
    try:
        results = await process_pending_results(
            result_repository=ExternalCommandResultRepository(pool),
            conversation_repository=ConversationRepository(pool),
            outbound_repository=OutboundMessageRepository(pool),
            limit=limit,
            transaction_repository=ExternalResultTransactionRepository(pool),
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_retries=max_retries,
        )
        return {
            "worker": "external_result_consumer",
            "mode": "once",
            "processed": len(results),
            "succeeded": sum(1 for result in results if result["status"] == "PROCESSED"),
            "failed": sum(1 for result in results if result["status"] == "FAILED"),
        }
    finally:
        pool.close()
        await pool.wait_closed()


async def maybe_recover_expired_leases(
    result_repository: ExternalCommandResultRepository,
    last_recovered_at: float | None,
    recover_interval_seconds: int,
    now: float | None = None,
) -> float | None:
    if recover_interval_seconds <= 0:
        return last_recovered_at
    now = time.monotonic() if now is None else now
    if last_recovered_at is not None and now - last_recovered_at < recover_interval_seconds:
        return last_recovered_at
    try:
        recovered = await result_repository.recover_expired_leases()
        if recovered:
            logger.info("Recovered %s expired external_command_result leases.", recovered)
    except Exception:
        logger.exception("Failed to recover expired external_command_result leases.")
    return now


async def run_forever(
    limit: int,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
) -> None:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-result-consumer",
        livechat_account_id="unused-for-external-result-consumer",
    )
    pool = await create_pool(settings)
    last_recovered_at = None
    try:
        result_repository = ExternalCommandResultRepository(pool)
        conversation_repository = ConversationRepository(pool)
        outbound_repository = OutboundMessageRepository(pool)
        transaction_repository = ExternalResultTransactionRepository(pool)
        while True:
            last_recovered_at = await maybe_recover_expired_leases(
                result_repository,
                last_recovered_at=last_recovered_at,
                recover_interval_seconds=recover_interval_seconds,
            )
            try:
                await process_pending_results(
                    result_repository=result_repository,
                    conversation_repository=conversation_repository,
                    outbound_repository=outbound_repository,
                    limit=limit,
                    transaction_repository=transaction_repository,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                    max_retries=max_retries,
                )
            except Exception:
                logger.exception("external_result_consumer polling iteration failed.")
            await asyncio.sleep(settings.poll_seconds)
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.once:
        result = asyncio.run(
            run_once(
                limit=args.limit,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                max_retries=args.max_retries,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        asyncio.run(
            run_forever(
                limit=args.limit,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                max_retries=args.max_retries,
                recover_interval_seconds=args.recover_interval_seconds,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
