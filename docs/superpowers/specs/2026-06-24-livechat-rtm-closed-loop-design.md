# LiveChat Polling-First Minimal Closed-Loop Design

Date: 2026-06-24
Status: Draft approved in chat, pending file review

## Goal

Build the smallest safe production path that proves this closed loop:

```text
LiveChat user message
        ->
polling fallback receiver
        ->
inbound_events
        ->
gateway consumer
        ->
conversation_states
        ->
outbound_messages
        ->
sender worker
        ->
LiveChat user receives fixed reply
```

This phase does not include SOP logic, LLM orchestration, backend fact lookup, Telegram handoff, or broad polling recovery.

## Success Criteria

- A LiveChat user message creates a `MESSAGE_CREATED` row in `inbound_events`.
- The gateway consumer processes that row exactly once when `ignored = 0`.
- A `conversation_states` record exists for the `chat_id`.
- An `outbound_messages` row is created for the reply.
- The sender worker sends the reply through LiveChat Agent Chat API and marks the row `SENT`.
- Self-authored agent/bot messages are persisted for audit but do not trigger new replies.

## Non-Goals

- No webhook production ingress yet. FastAPI is scaffolded only as the future host surface.
- No migration framework. Schema setup uses raw SQL and an idempotent bootstrap command.
- No generic rules engine, no LangGraph, no RAG, and no payment or account fact inference.
- No broad polling recovery. Polling is the active ingress in this phase, but it must stay bounded and receiver-shaped.

## Architecture

Use a single repo with shared application code under `src/app/`.

```text
src/app/
  api/
  channels/livechat/
  core/
  db/
  schemas/
  services/
  workers/
```

### `api/`

Contains a minimal FastAPI app with health endpoints and placeholder routing structure for future webhook ingress. It must not participate in the current polling-based event flow.

### `channels/livechat/`

Contains:

- polling receiver logic
- RTM WebSocket receiver logic placeholder
- incoming payload normalization
- sender-side LiveChat HTTP client helpers
- channel-specific schema helpers

This module owns LiveChat protocol details and hides them from gateway and state services.

### `core/`

Contains typed settings, logging helpers, and shared constants such as supported RTM actions and outbound status values.

### `db/`

Contains:

- MySQL connection pool creation
- SQL bootstrap loader
- repository helpers for inbound events, conversations, and outbox

The first version can use direct SQL repository functions rather than a full ORM.

### `schemas/`

Contains internal typed models for inbound events, conversation state updates, and outbound messages. These models define the application contracts between workers and services.

### `services/`

Contains:

- `ConversationService`
- `OutboundMessageService`
- `GatewayService`

Business flow decisions live here, but only deterministic v1 logic is allowed in this phase.

### `workers/`

Contains thin process entrypoints:

- `polling_receiver`
- `gateway_consumer`
- `sender_worker`
- `bootstrap_db`

Each worker should compose shared services rather than duplicate logic.

## Data Model

### `inbound_events`

Retain the current role:

- durable ingress inbox
- deduplication boundary
- replay and audit surface

Expected fields remain aligned with the current RTM receiver draft, with `source` distinguishing ingress type:

- source
- raw_action
- organization_id
- chat_id
- thread_id
- event_id
- event_type
- standard_event_type
- author_id
- sender_role
- occurred_at
- dedup_key
- payload_json
- ignored
- ignore_reason
- processed

### `conversation_states`

Use the schema from the handoff note. `chat_id` is unique for this phase. `conversation_id` should be stable and derived as `livechat:{chat_id}` unless later requirements force a different rule.

Required service operations:

- `get_or_create_by_chat_id`
- `update_current_thread_id`
- `update_last_inbound_event_id`
- `update_last_outbound_message_id`
- `update_status`
- `update_active_workflow`

### `outbound_messages`

Use the schema from the handoff note. First version supports only:

- `action_type = send_event`
- `message_type = text`
- `status in PENDING | SENT | FAILED`

Retry metadata is stored now even if first-pass retry behavior is conservative.

## Runtime Flow

### RTM Receiver

Responsibilities:

- load settings from `.env`
- connect to MySQL
- connect and login to LiveChat RTM
- send protocol-level pings
- reconnect with backoff where allowed
- normalize supported push events
- derive dedup keys
- store normalized rows into `inbound_events`

Rules:

- Receiver never calls gateway logic, sender logic, LLMs, SOPs, or backend capabilities.
- `incoming_chat` initial thread events are split and inserted as separate normalized rows.
- Self-authored events are stored with `ignored = 1` and `ignore_reason = self_message`.
- Unsupported actions are stored with `standard_event_type = UNSUPPORTED` and ignored for orchestration.

### Polling Fallback Receiver

Responsibilities:

- load settings from `.env`
- connect to MySQL
- call LiveChat list APIs needed to discover recent candidate chats or events
- inspect only a bounded recent window
- normalize supported message events into the same internal schema as RTM
- derive dedup keys using the same shared logic
- store normalized rows into `inbound_events` with `source = polling_fallback`

Rules:

- Polling receiver is an ingress adapter only. It never calls gateway logic, sender logic, LLMs, SOPs, or backend capabilities.
- Polling must not directly send replies.
- Polling must not create a separate deduplication scheme from RTM/webhook-compatible normalization.
- Polling must preserve self-authored filtering so agent echoes do not recurse.
- Polling must prefer targeted, time-bounded discovery and must not become an unbounded full-list scanner.

### Gateway Consumer

Responsibilities:

- poll `inbound_events` where `processed = 0` and `ignored = 0`
- process rows in ascending id order
- load or create conversation state by `chat_id`
- update `current_thread_id` and `last_inbound_event_id`
- generate a deterministic fixed reply only for `MESSAGE_CREATED`
- write the reply to `outbound_messages`
- mark inbound row `processed = 1`

Rules:

- Gateway never sends directly to LiveChat.
- Non-message events may still update conversation state but do not enqueue a reply in v1.
- Processing order must ensure that an outbox row is inserted before an inbound row is marked processed.

### Sender Worker

Responsibilities:

- poll `outbound_messages` where `status = PENDING`
- send text replies through LiveChat Agent Chat API
- mark success with `status = SENT` and `sent_at`
- mark failure with `status = FAILED` and store `last_error`

Rules:

- First version sends only text message payloads.
- First version does not implement `add_user_to_chat` unless the API proves it is required for delivery in this environment.
- Retry handling is stored in schema, but implementation may begin with no automatic retry beyond leaving truly transient classification for a later step.

## Deterministic Reply Rule

The first reply rule is intentionally fixed:

```text
If standard_event_type = MESSAGE_CREATED
Reply: Hello, I received your message. How can I help you today?
```

No other event type generates an outbound reply in this phase.

## Safety Boundaries

- Polling fallback is the only active source of customer messages in this phase.
- LLM or RAG components must not be introduced into this path.
- No business facts may be inferred from chat content.
- Self-authored outbound echoes must not produce recursive replies.
- Failures must be visible in persisted tables rather than hidden in logs only.
- Future RTM or webhook ingress must reuse the same normalization and inbox contracts rather than fork behavior.

## Key Risks And Controls

### Duplicate first-message processing

Risk:
`incoming_chat.initial_event` and later `incoming_event` could represent the same user message.

Control:
Use stable dedup keys based on `chat_id`, `thread_id`, and `event_id` when available, with payload hash fallback only when identifiers are absent.

### Partial processing

Risk:
Gateway could create an outbox row but fail before marking inbound processed, or mark inbound processed before outbox insert.

Control:
Repository methods should support transaction boundaries for gateway processing so state update, outbox insert, and processed flag changes are applied coherently.

### Polling duplication and drift

Risk:
Polling may repeatedly re-read the same chat window, miss ordering edges across chats, or grow into a noisy full scan.

Control:
Use a bounded polling cursor strategy with shared dedup keys and explicit receiver state, such as storing last seen timestamps and stable event identifiers where available. Keep the polling window short and deterministic.

### Recursive self-reply

Risk:
Outbound agent messages received back over RTM could be re-consumed as customer input.

Control:
Author allowlist filtering remains enforced in normalization and is preserved in the persisted row.

### Sender invisibility

Risk:
Delivery failures could disappear if only logged.

Control:
Every send attempt updates the outbox row with either `SENT` metadata or a stored `last_error`.

## Testing Strategy

### Unit tests

- RTM payload normalization for supported actions
- polling payload normalization for supported actions
- `incoming_chat` initial event splitting
- dedup key generation behavior
- self-message ignore behavior
- gateway decision to enqueue only for `MESSAGE_CREATED`
- sender state transitions on success and failure

### Integration tests

- bootstrap creates required tables idempotently
- processing one stored inbound message creates one conversation state row and one outbound row
- processing ignored inbound rows does not create outbound rows
- polling receiver writes normalized rows into `inbound_events` without duplicating the same source event

Tests should prefer isolated fixtures and local MySQL-backed verification only where behavior depends on SQL semantics.

## Delivery Sequence

1. Create repo structure and shared settings/bootstrap code.
2. Add SQL bootstrap files for `inbound_events`, `conversation_states`, and `outbound_messages`.
3. Implement repository and service layers.
4. Add polling receiver in `src/app/channels/livechat/` and `src/app/workers/polling_receiver.py`.
5. Add gateway consumer worker.
6. Add sender worker and LiveChat HTTP client.
7. Add minimal FastAPI scaffold and health endpoint.
8. Preserve RTM-compatible normalization boundaries so WebSocket ingress can be added later without changing downstream services.
9. Add tests for risky behavior.
10. Run local verification for bootstrap and targeted unit/integration tests.

## Deferred Work

Explicitly deferred until after the minimal closed loop is proven:

- webhook ingress activation
- RTM WebSocket ingress activation
- targeted polling recovery
- Telegram handoff
- attachments and screenshots
- withdrawal SOP
- capability calls to backend systems
- LLM or LangGraph orchestration
