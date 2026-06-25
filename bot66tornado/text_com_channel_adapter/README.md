# Text.com / LiveChat Webhook + Channel Adapter Minimal Implementation

This project implements the first two layers of the AI customer service architecture:

1. Webhook receiver for Text.com / LiveChat `incoming_chat` and `incoming_event`.
2. Channel Adapter that normalizes platform-specific payloads into canonical inbound events.

It intentionally does not implement SOP, KB retrieval, Skill execution, or LangGraph workflow. Those should live behind the `MessageIngestionService._handoff_to_gateway()` seam.

## Directory

```text
text_com_channel_adapter/
├── app/
│   ├── main.py
│   ├── api/
│   │   └── routes/
│   │       └── text_com_webhook.py
│   ├── application/
│   │   └── message_ingestion.py
│   ├── channels/
│   │   ├── base.py
│   │   └── text_com/
│   │       ├── adapter.py
│   │       ├── client.py
│   │       └── models.py
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── security.py
│   ├── domain/
│   │   └── messages.py
│   └── infrastructure/
│       └── idempotency/
│           └── memory.py
├── tests/
│   └── test_text_com_adapter.py
├── .env.example
└── requirements.txt
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/healthz
```

Main webhook endpoint:

```text
POST /api/v1/webhooks/text-com
```

Optional per-action aliases:

```text
POST /api/v1/webhooks/text-com/incoming-chat
POST /api/v1/webhooks/text-com/incoming-event
```

## Test incoming_event

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/text-com \
  -H 'Content-Type: application/json' \
  -d '{
    "webhook_id": "wh_1",
    "secret_key": "replace-me",
    "action": "incoming_event",
    "organization_id": "org_1",
    "payload": {
      "chat_id": "PJ0MRSHTDG",
      "thread_id": "K600PKZON8",
      "event": {
        "id": "K600PKZON8_1",
        "type": "message",
        "text": "hello",
        "author_id": "customer_1",
        "created_at": "2026-06-22T12:00:00Z"
      }
    }
  }'
```

Expected response:

```json
{
  "ok": true,
  "action": "incoming_event",
  "normalized_events": 1,
  "accepted": 1,
  "duplicated": 0,
  "skipped": 0
}
```

## Production notes

- Replace `InMemoryIdempotencyStore` with Redis `SET NX` + TTL or a database unique key.
- Replace `DEFAULT_TENANT_ID` with a database-backed TenantConfigService mapping `organization_id` to `tenant_id`, channel credentials, SOP, and knowledge base.
- Do not put AI orchestration in the webhook route. Persist to `message_outbox` or publish to a message queue first.
- Configure HTTPS and keep `TEXT_COM_WEBHOOK_SECRET` enabled. Text.com includes the webhook secret in the payload; this is not an HMAC signature.
- Configure `TEXT_COM_IGNORED_AUTHOR_IDS` to avoid the bot consuming its own sent messages.
