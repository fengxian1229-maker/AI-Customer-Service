import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import GatewayTransactionRepository, InboundEventRepository
from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService


async def process_next_batch(pool, limit: int = 20) -> list[dict]:
    inbound_repository = InboundEventRepository(pool)
    transactional_repository = GatewayTransactionRepository(pool, inbound_repository=inbound_repository)
    service = GatewayService(transactional_repository=transactional_repository)

    results = []
    rows = await inbound_repository.fetch_unprocessed(limit=limit)
    for row in rows:
        inbound_event_id = row.pop("id")
        event = InboundEvent(**row)
        results.append(await service.process_event(inbound_event_id, event))
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume inbound_events into conversation state and outbox.")
    parser.add_argument("--once", action="store_true", help="Run one gateway batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum inbound events to process.")
    return parser


async def run_once(limit: int) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-gateway",
        livechat_account_id="unused-for-gateway",
    )
    pool = await create_pool(settings)
    try:
        results = await process_next_batch(pool, limit=limit)
        return {
            "worker": "gateway_consumer",
            "mode": "once",
            "processed": len(results),
            "enqueued": sum(1 for result in results if result.get("outbound_message")),
        }
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_once(limit=args.limit))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
