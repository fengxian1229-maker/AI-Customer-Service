AI Customer Service MVP
=======================

Polling-first LiveChat/Text.com customer-service MVP.

Current scope:

- Poll LiveChat Agent Chat API for allowed groups.
- Normalize customer `message` and `file` events.
- Store events in MySQL `inbound_events`.
- Consume inbound events into `conversation_states` and `outbound_messages`.
- Send pending text replies through LiveChat `send_event`.

Setup
-----

```bash
uv sync --group dev
cp .env.example .env
```

Fill `.env` with LiveChat and MySQL credentials. For the current test scope, keep:

```env
LIVECHAT_ALLOWED_GROUP_IDS=23
```

Run Tests
---------

```bash
uv run --group dev pytest tests/unit -v
```

Bootstrap Database
------------------

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.bootstrap_db
```

Poll LiveChat Once
------------------

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.polling_receiver
```

Run Gateway Once
----------------

```bash
PYTHONPATH=src uv run --group dev python - <<'PY'
import asyncio
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.workers.gateway_consumer import process_next_batch

async def main():
    pool = await create_pool(Settings())
    try:
        print(await process_next_batch(pool, limit=20))
    finally:
        pool.close()
        await pool.wait_closed()

asyncio.run(main())
PY
```

Run Sender Once
---------------

```bash
PYTHONPATH=src uv run --group dev python - <<'PY'
import asyncio
from app.channels.livechat.sender_client import LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import OutboundMessageRepository
from app.workers.sender_worker import process_pending_message

async def main():
    settings = Settings()
    pool = await create_pool(settings)
    try:
        repo = OutboundMessageRepository(pool)
        client = LiveChatSenderClient(
            settings.livechat_api_base,
            settings.livechat_account_id,
            settings.livechat_agent_access_token,
        )
        for message in await repo.fetch_pending(limit=20):
            print(await process_pending_message(repo, client, message))
    finally:
        pool.close()
        await pool.wait_closed()

asyncio.run(main())
PY
```

Notes
-----

- `.env` is ignored by Git and must not be committed.
- The current polling receiver filters by `LIVECHAT_ALLOWED_GROUP_IDS`.
- `get_chat` is used when available. If LiveChat returns a permission error, the receiver falls back to `list_chats.last_event_per_type`.
