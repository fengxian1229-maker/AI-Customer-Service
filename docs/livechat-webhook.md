# LiveChat / Text.com Webhook

This webhook server receives Text.com / LiveChat Chat Webhooks, validates the shared secret, normalizes events into `InboundEvent`, and writes them to `inbound_events`.

It does not call LangGraph or send LiveChat replies directly. The existing `gateway_consumer -> GatewayService -> outbound_messages -> sender_worker` chain continues to handle replies.

## Configuration

Add the webhook secret and group allow-list to `.env`:

```dotenv
LIVECHAT_WEBHOOK_ENABLED=true
LIVECHAT_WEBHOOK_SECRET=replace-with-the-secret-from-text-console
LIVECHAT_ALLOWED_GROUP_IDS=23
WEBHOOK_SERVER_HOST=0.0.0.0
WEBHOOK_SERVER_PORT=8000
```

The server also needs the existing MySQL settings. If `incoming_event` payloads do not include group data, the server may call `get_chat`, so keep the existing LiveChat API settings available too.

## Start the Server

```bash
uv run python -m app.workers.webhook_server
```

The endpoint is:

```text
POST /api/v1/webhooks/livechat
```

Keep the main worker chain running separately:

```bash
uv run python -m app.workers.service_runner --all
```

## Register Webhooks

Register these Chat Webhook actions in Text.com Developer Console or through the Configuration API:

```text
incoming_chat
incoming_event
incoming_rich_message_postback
chat_deactivated
chat_transferred
user_removed_from_chat
```

Use your public URL that forwards to `/api/v1/webhooks/livechat`. The webhook JSON body must include the top-level `secret_key` configured in `LIVECHAT_WEBHOOK_SECRET`.

## Local Simulation

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/webhooks/livechat \
  -H 'Content-Type: application/json' \
  -d '{
    "webhook_id": "local-1",
    "secret_key": "replace-with-the-secret-from-env",
    "action": "incoming_event",
    "organization_id": "org-1",
    "payload": {
      "chat_id": "chat-1",
      "thread_id": "thread-1",
      "access": {"group_ids": [23]},
      "event": {
        "id": "event-1",
        "type": "message",
        "author_id": "customer-1",
        "created_at": "2026-07-06T00:00:00Z",
        "text": "hello"
      }
    }
  }'
```

Expected response:

```json
{"ok":true,"action":"incoming_event","normalized":1,"inserted":1,"duplicates":0,"ignored":0}
```

## Verify Inbound Events

```sql
SELECT id, source, raw_action, chat_id, thread_id, event_id, ignored, ignore_reason
FROM inbound_events
WHERE source = 'livechat_webhook'
ORDER BY id DESC
LIMIT 10;
```

Repeating the same curl should return `duplicates: 1`, because `dedup_key` is unique.
