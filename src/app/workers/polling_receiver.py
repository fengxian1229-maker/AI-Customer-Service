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
    inserted = await poll_once(
        client=StaticChatClient(listed, client),
        repository=repository,
        self_author_ids=self_author_ids,
        limit=limit,
        allowed_group_ids=allowed_group_ids,
    )
    return {
        "listed": len(listed),
        "inserted": len(inserted),
        "events": inserted,
    }


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


if __name__ == "__main__":
    import asyncio
    import json

    result = asyncio.run(smoke_test_polling())
    print(json.dumps({
        "listed": result["listed"],
        "inserted": result["inserted"],
        "event_ids": [event.event_id for event in result["events"]],
    }, ensure_ascii=False, indent=2))


__all__ = ["ReceiverState", "build_receiver_state", "run_polling_cycle", "smoke_test_polling"]
