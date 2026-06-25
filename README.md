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
PYTHONPATH=src LIVECHAT_ALLOWED_GROUP_IDS=23 uv run --group dev python -m app.workers.polling_receiver --once --groups 23 --limit 20
```

Run Gateway Once
----------------

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.gateway_consumer --once --limit 20
```

Run Sender Once
---------------

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.sender_worker --once --limit 20
```

Safe Group 23 Smoke
-------------------

```bash
scripts/smoke_livechat_group23.sh
```

Notes
-----

- `.env` is ignored by Git and must not be committed.
- The current polling receiver requires explicit `--groups` or `LIVECHAT_ALLOWED_GROUP_IDS`; do not run broad all-group polling.
- `get_chat` is used when available. If LiveChat returns a permission error, the receiver falls back to `list_chats.last_event_per_type`.
