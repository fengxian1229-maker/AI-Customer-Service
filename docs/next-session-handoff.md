# AI Customer Service MVP Next Session Handoff

Date: 2026-06-24
Repository: `https://github.com/fengxian1229-maker/AI-Customer-Service.git`
Local path: `/Users/andy/ai-agent`

## Copy This Prompt Into A New Codex Session

```text
You are continuing the AI Customer Service MVP in `/Users/andy/ai-agent`.

Read these first:
- `/Users/andy/ai-agent/README.md`
- `/Users/andy/ai-agent/docs/next-session-handoff.md`
- `/Users/andy/ai-agent/docs/superpowers/specs/2026-06-24-livechat-rtm-closed-loop-design.md`
- `/Users/andy/Downloads/New project 2/bot66tornado/docs/AI智能客服MVP技术方案.md`

Current goal:
Continue from the polling-first LiveChat MVP that already proved this loop:
LiveChat polling -> inbound_events -> gateway_consumer -> conversation_states/outbound_messages -> sender_worker -> LiveChat send_event.

Important current constraints:
- Only poll LiveChat group 23 for now unless I explicitly change it.
- Do not use broad all-group polling.
- Do not send direct replies outside the outbox/sender worker except for explicit smoke tests.
- Do not let LLM/RAG decide payment, withdrawal, account, turnover, or backend facts.
- Keep all facts from deterministic code, LiveChat, Telegram staff, backend API, or stored state.
- Keep `.env` out of Git.

Before coding:
1. Inspect the latest local files.
2. Run `uv run --group dev pytest tests/unit -v`.
3. Check current MySQL table contents before polling or sending.
4. Confirm whether I want to clear test data before running a new end-to-end smoke.

Recommended next task:
Make the current workers production-usable:
- add proper CLI entrypoints for polling, gateway, and sender
- persist group_id/users metadata into `inbound_events.payload_json`
- reduce duplicate warning noise from `INSERT IGNORE`
- add sender error classification
- add a safe one-command local smoke script for group 23 only
```

## Current Polling-First Worker Status

This session hardened the polling-first worker path without adding websocket or webhook ingress.

Implemented:

- `python -m app.workers.polling_receiver --once --groups 23 --limit 20`
- `python -m app.workers.gateway_consumer --once --limit 20`
- `python -m app.workers.sender_worker --once --limit 20`
- explicit polling group parsing from `--groups` or `LIVECHAT_ALLOWED_GROUP_IDS`
- refusal to run polling when no explicit group source is provided
- duplicate-aware inbound insert using `ON DUPLICATE KEY UPDATE id = id`
- polling audit metadata in `inbound_events.payload_json`
- sender statuses: `SENT`, `FAILED_CONFIG`, `RETRYABLE`, `FAILED_BUSINESS`, `FAILED_UNKNOWN`
- `scripts/smoke_livechat_group23.sh`

Ingress staging remains:

- 前期: Polling 主入口。
- 中期: WebSocket 主入口，Polling 用于断线补偿、指定 chat 补拉、本地调试。
- 后期: Webhook 正式主入口，Polling 用于 targeted fallback、异常恢复、排障、数据校验。

TODO for later phases only:

- WebSocket RTM receiver
- Webhook receiver and signature verification
- webhook registration docs
- LangGraph, RAG, LLM automatic replies
- Telegram full handoff loop
- backend API fact lookup and withdrawal workflows

## What Is Already Done

### Repository And Project Setup

- Code has been moved to `/Users/andy/ai-agent`.
- Git remote is connected to `https://github.com/fengxian1229-maker/AI-Customer-Service.git`.
- `main` has been pushed.
- `.env.example` exists.
- `.env`, `.venv`, pytest cache, and Python cache files are ignored.
- Dependency management uses `uv`.

### Implemented Python Structure

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

Key files:

- `src/app/channels/livechat/sender_client.py`
- `src/app/channels/livechat/polling_receiver.py`
- `src/app/channels/livechat/normalizer.py`
- `src/app/workers/polling_receiver.py`
- `src/app/workers/gateway_consumer.py`
- `src/app/workers/sender_worker.py`
- `src/app/db/repositories.py`
- `src/app/db/bootstrap.py`
- `src/app/services/gateway.py`

### Database Tables

SQL bootstrap files exist:

- `sql/001_inbound_events.sql`
- `sql/002_conversation_states.sql`
- `sql/003_outbound_messages.sql`

The bootstrap also includes compatibility fixes for an older `inbound_events` table shape.

Current local working DB used during testing:

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_DATABASE=livechat_ai
```

The originally mentioned `ai_customer_service` database did not exist on this machine during testing.

### LiveChat Polling

Implemented:

- `list_chats`
- `get_chat`
- group allowlist filtering through `LIVECHAT_ALLOWED_GROUP_IDS`
- no default polling group; polling requires `--groups` or `LIVECHAT_ALLOWED_GROUP_IDS`
- customer `message` -> `MESSAGE_CREATED`
- customer `file` -> `FILE_RECEIVED`
- self/agent filtering based on user type and `LIVECHAT_SELF_AUTHOR_IDS`
- dedup key generation
- MySQL duplicate-aware insert with `ON DUPLICATE KEY UPDATE id = id`
- fallback from `get_chat` to `list_chats.last_event_per_type` when `get_chat` lacks permission

Important verified behavior:

- The LiveChat token supplied by the user is already a pre-encoded Basic credential.
- The code now detects pre-encoded Basic tokens and does not double-encode them.
- The previous accidental group 15 pull happened because polling was not group-filtered yet.
- Group filtering was fixed. Current polling must use `LIVECHAT_ALLOWED_GROUP_IDS=23` for test scope.

### Verified LiveChat Group 23 Smoke

After group filtering was added, one successful group 23 pull wrote:

```text
chat_id=TH14PNI6DT
thread_id=TH14PPVRY5
event_id=TH14PPVRY5_6
standard_event_type=MESSAGE_CREATED
processed=0
group_id=23
agent=Lingxi <lingxi@goetm.com>
```

### Verified Full Program Loop

The full program loop was run once:

```text
inbound_events
  -> gateway_consumer.process_next_batch()
  -> conversation_states
  -> outbound_messages
  -> sender_worker.process_pending_message()
  -> LiveChat send_event
```

Observed DB state after the run:

```text
inbound_events_count=1
conversation_states_count=1
outbound_messages_count=1

inbound:
standard_event_type=MESSAGE_CREATED
chat_id=TH14PNI6DT
thread_id=TH14PPVRY5
event_id=TH14PPVRY5_6
processed=1

outbound:
status=SENT
conversation_id=livechat:TH14PNI6DT
last_error=None
```

The gateway currently sends a fixed reply:

```text
Hello, I received your message. How can I help you today?
```

### Tests

Current test command:

```bash
uv run --group dev pytest tests/unit -v
```

Last verified result:

```text
20 passed
```

## What Is Not Done

### Production Ingress

Not done:

- LiveChat webhook receiver
- webhook signature/auth verification
- webhook registration scripts/docs
- RTM WebSocket production receiver integration
- targeted polling recovery design

Current ingress is temporary polling-first. MVP docs say webhook should become the official ingress and polling should become targeted fallback.

### Polling Hardening

Not done:

- persistent polling cursor
- per-group/per-chat polling state
- bounded time-window controls
- proper duplicate warning suppression
- structured run logs
- retry/backoff around LiveChat API calls
- CLI flags such as `--once`, `--limit`, `--groups`

Current polling is usable for local smoke only.

### Gateway And Conversation State

Partially done:

- creates/loads `conversation_states`
- marks inbound processed
- creates `outbound_messages`
- fixed reply for `MESSAGE_CREATED`

Not done:

- workflow state machine
- active workflow continuation
- slot memory updates
- handoff state transitions
- last outbound pointer updates
- transactional processing across inbound/conversation/outbox
- idempotent outbox creation if gateway crashes after insert

### Sender Worker

Partially done:

- sends text message via LiveChat `send_event`
- marks outbound `SENT`
- treats returned `event_id` as success

Not done:

- `add_user_to_chat` before send
- retry strategy
- error classification
- marking retryable vs permanent failures
- handling inactive/closed chat
- structured send audit

### SOP / AI / RAG

Not done:

- Orchestrator
- LangGraph / GraphState
- FAQ routing
- withdrawal_issue_v1
- slot collection for account/order/screenshot
- knowledge base or RAG
- LLM response generation

Important boundary:

LLM/RAG must not generate payment, withdrawal, turnover, account, arrival, or backend status facts.

### Attachments And Screenshots

Partially done:

- file events can be normalized as `FILE_RECEIVED`

Not done:

- image-only filtering
- attachment metadata table
- screenshot binding to conversation state
- duplicate attachment prevention
- forwarding attachments to Telegram
- safe storage/reference handling

### Backend Capability

Not done:

- `query_withdrawal_case_facts`
- Tiancheng backend API client
- backend result normalization
- customer-safe summary
- fact flags
- raw result storage/ref
- human handoff decision based on deterministic facts

### Telegram Human Handoff

Not done:

- Telegram bot integration in this repo
- Telegram group master card
- reply-to mapping
- staff replies back to LiveChat
- handoff failure recovery
- `HANDOFF_REQUESTED`, `HUMAN_RELAYING`, `RELAY_FAILED` transitions

## Recommended Next Steps

### Step 1: Make Local Workers Operable

Goal:
Turn the current functions into clear commands that can be run repeatedly without copy-paste Python snippets.

Implement:

- `python -m app.workers.polling_receiver --once --groups 23 --limit 20`
- `python -m app.workers.gateway_consumer --once --limit 20`
- `python -m app.workers.sender_worker --once --limit 20`
- command output with counts and IDs
- no secrets in logs

Why first:
The next developer needs repeatable operations before adding SOP complexity.

### Step 2: Persist Group/User Metadata

Goal:
Every inbound row should contain enough audit context to answer:

- which LiveChat group
- which customer
- which agents were in the chat
- whether the event came from full `get_chat` or summary fallback

Implement:

- add `group_ids`, `chat_users`, `polling_source`, and `last_thread_summary` to `payload_json`
- consider dedicated columns later, but payload metadata is enough for the next smoke
- add tests

Reason:
The first implementation pulled a group 15 chat before group filtering existed. Metadata would have made that obvious from DB alone.

### Step 3: Reduce Duplicate Noise

Goal:
Avoid MySQL warnings from expected duplicate events during repeated polling.

Implement:

- use `INSERT ... ON DUPLICATE KEY UPDATE id = id`
- or suppress duplicate warnings explicitly
- report inserted vs duplicate counts

### Step 4: Make Sender Safer

Goal:
Do not mark all send failures the same way.

Implement:

- classify auth/scope errors as configuration failures
- classify transient network/timeouts as retryable
- classify inactive/closed chat as business failure
- store `last_error`
- increment `retry_count`

### Step 5: Start Minimal `withdrawal_issue_v1`

Only after worker operations are stable.

Implement a deterministic first version:

- detect withdrawal/not received intent with rules or constrained classifier
- collect account/order/screenshot slots
- do not call backend yet unless API contract is available
- if facts missing, ask for missing slot
- if unsupported/ambiguous, route to human handoff placeholder

## Current Commands

Install:

```bash
cd /Users/andy/ai-agent
uv sync --group dev
```

Run tests:

```bash
uv run --group dev pytest tests/unit -v
```

Bootstrap DB:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.bootstrap_db
```

Poll once:

```bash
PYTHONPATH=src LIVECHAT_ALLOWED_GROUP_IDS=23 uv run --group dev python -m app.workers.polling_receiver --once --groups 23 --limit 20
```

## Important Environment Notes

The user-provided LiveChat token is already a base64 Basic credential. Do not double-encode it.

The local shell previously had conflicting LiveChat env vars:

```text
LIVECHAT_ACCOUNT_ID was set to a conflicting value
```

Use explicit `.env` values or override env vars when testing.

The local working database during successful tests was:

```text
MYSQL_DATABASE=livechat_ai
```

## Quality Rules For Next Session

- Inspect latest files before changing code.
- Use tests before behavior changes.
- Keep group allowlist enabled.
- Do not send customer replies directly unless the user explicitly requests a smoke test.
- Prefer outbox/sender path for real replies.
- Do not introduce LLM/RAG into payment/account facts.
- State remaining risk after every smoke.
