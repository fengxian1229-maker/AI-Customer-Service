import argparse
import asyncio
import json
import os
from pathlib import Path

from app.channels.livechat.polling_receiver import ReceiverState, build_receiver_state, poll_once
from app.channels.livechat.sender_client import LiveChatSenderClient
from app.core.settings import Settings
from app.db.bootstrap import bootstrap_database
from app.db.mysql import create_pool
from app.db.repositories import InboundEventRepository


async def run_polling_cycle(
    client,
    repository,
    self_author_ids: set[str],
    limit: int = 20,
    allowed_group_ids: set[int] | None = None,
) -> dict:
    listed = await client.list_chats(limit=limit)
    stats = {
        "listed": len(listed),
        "matched_group": 0,
        "inserted": 0,
        "duplicates": 0,
        "ignored_self": 0,
        "ignored_agent": 0,
    }
    inserted = await poll_once(
        client=StaticChatClient(listed, client),
        repository=repository,
        self_author_ids=self_author_ids,
        limit=limit,
        allowed_group_ids=allowed_group_ids,
        stats=stats,
    )
    stats["events"] = inserted
    return stats


class StaticChatClient:
    def __init__(self, listed: list[dict], client) -> None:
        self._listed = listed
        self._client = client

    async def list_chats(self, limit: int = 20) -> list[dict]:
        return self._listed[:limit]

    async def get_chat(self, chat_id: str) -> dict:
        return await self._client.get_chat(chat_id)


async def smoke_test_polling() -> dict:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        await bootstrap_database(pool, Path("sql"))
        repository = InboundEventRepository(pool)
        client = LiveChatSenderClient(
            base_url=settings.livechat_api_base,
            account_id=settings.livechat_account_id,
            access_token=settings.livechat_agent_access_token,
        )
        result = await run_polling_cycle(
            client=client,
            repository=repository,
            self_author_ids=settings.livechat_self_author_id_set,
            limit=settings.poll_limit,
            allowed_group_ids=settings.livechat_allowed_group_id_set,
        )
        return result
    finally:
        pool.close()
        await pool.wait_closed()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll LiveChat allowed groups into inbound_events.")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum chats to list in one cycle.")
    parser.add_argument("--groups", help="Comma-separated LiveChat group ids, for example 23 or 23,0.")
    return parser


def parse_group_ids(cli_value: str | None, env_value: str | None = None) -> set[int]:
    raw = cli_value if cli_value is not None and cli_value.strip() else env_value
    if raw is None or not raw.strip():
        raise ValueError(
            "Refusing to poll LiveChat without explicit groups. "
            "Pass --groups or set LIVECHAT_ALLOWED_GROUP_IDS."
        )
    group_ids = {
        int(item.strip())
        for item in raw.split(",")
        if item.strip()
    }
    if not group_ids:
        raise ValueError(
            "Refusing to poll LiveChat without explicit groups. "
            "Pass --groups or set LIVECHAT_ALLOWED_GROUP_IDS."
        )
    return group_ids


async def run_once(limit: int, groups: set[int]) -> dict:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        repository = InboundEventRepository(pool)
        client = LiveChatSenderClient(
            base_url=settings.livechat_api_base,
            account_id=settings.livechat_account_id,
            access_token=settings.livechat_agent_access_token,
        )
        result = await run_polling_cycle(
            client=client,
            repository=repository,
            self_author_ids=settings.livechat_self_author_id_set,
            limit=limit,
            allowed_group_ids=groups,
        )
        return {
            "worker": "polling_receiver",
            "mode": "once",
            "groups": sorted(groups),
            "listed": result["listed"],
            "matched_group": result["matched_group"],
            "inserted": result["inserted"],
            "duplicates": result["duplicates"],
            "ignored_self": result["ignored_self"],
            "ignored_agent": result["ignored_agent"],
        }
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        groups = parse_group_ids(args.groups, os.getenv("LIVECHAT_ALLOWED_GROUP_IDS"))
    except ValueError as exc:
        print(json.dumps({"worker": "polling_receiver", "error": str(exc)}, ensure_ascii=False))
        return 2

    result = asyncio.run(run_once(limit=args.limit, groups=groups))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ReceiverState",
    "build_arg_parser",
    "build_receiver_state",
    "parse_group_ids",
    "run_polling_cycle",
    "smoke_test_polling",
]
