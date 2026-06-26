# AI Customer Service MVP Next Session Handoff

Date: 2026-06-26
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
P0 ingress contract is complete. P1 graph failure boundaries and P2 conversation history are complete. P3-A introduced the LangGraph checkpointer injection boundary and per-conversation thread config. P3-B added a checkpoint provider boundary with `off` and local `memory` modes plus read-only graph debug helpers. P4-A added minimal deterministic KB-backed RAG. P4-B connects DB-backed `knowledge_documents` retrieval into the RAG path through GatewayService/RagService injection. P4-C adds tenant/kb-scope knowledge management, deterministic ranking v1, source-file seeding, and the lightweight knowledge admin CLI. P5-A adds durable checkpoint design, checkpoint metadata schema/bootstrap, a `graph_checkpoint_runs` repository, and a conservative `mysql` checkpoint mode boundary. P5-A.1 wires `GraphCheckpointRunRepository` into `gateway_consumer -> GatewayService` for lightweight runtime metadata only. P5-B enables a real sync MySQL LangGraph saver path with `PyMySQLSaver`, explicit saver setup, and batch-lifetime checkpointer management in `gateway_consumer`. P5-C adds a read-only checkpoint admin CLI over `graph_checkpoint_runs` and `graph_run_errors`. P5-D changes DB-backed RAG prefetching to FAQ-only lazy retrieval. P6-A adds a model-provider boundary with mock rewrite shadow and mock intent shadow. P6-B adds a real Gemini Vertex AI shadow provider for rewrite and intent only. P6-B.1 adds shadow output guardrails and a standalone smoke review tool.

Important current constraints:
- Only poll LiveChat group 23 for now unless I explicitly change it.
- Do not use broad all-group polling.
- Do not send direct replies outside the outbox/sender worker except for explicit smoke tests.
- Do not let LLM/RAG decide payment, withdrawal, account, turnover, or backend facts.
- Backend-fact questions must return a safe fallback and must not query `knowledge_documents`, even if they pass through RagService guardrail logic.
- Keep all facts from deterministic code, LiveChat, Telegram staff, backend API, or stored state.
- Keep `.env` out of Git.
- Normal FAQ/RAG path must only emit `livechat.send_text`; do not emit `RAG_PLACEHOLDER` or write `external_commands`.

Before coding:
1. Inspect the latest local files.
2. Run `uv run --group dev pytest tests/unit -v`.
3. Check current MySQL table contents before polling or sending.
4. Confirm whether I want to clear test data before running a new end-to-end smoke.

Recommended next task:
- build checkpoint Web admin or richer debug UX on top of the new read-only CLI / repository boundary
- or verify Gemini shadow quality and failure handling without placing real LLM calls inside graph nodes
- Keep polling-first; do not add WebSocketReceiver or WebhookReceiver in the same change.
- Do not add vector DB, embeddings, LLM answer generation, or interrupt/resume in the same change.
```

## Latest P6-B.1 Status

- Added `tests/integration/test_mysql_checkpoint_persistence.py`
- Added `tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py`
- Added `tests/integration/test_checkpoint_admin_mysql_smoke.py`
- Added `src/app/llm/contracts.py`, `src/app/llm/provider.py`, `src/app/llm/mock_provider.py`, and `src/app/llm/__init__.py`
- Added `src/app/llm/gemini_model.py` and `src/app/llm/gemini_provider.py`
- Added `src/app/llm/guardrails.py`
- Added `src/app/workers/gemini_shadow_smoke.py`
- Added `llm_provider`, `llm_rewrite_shadow_enabled`, `llm_rewrite_fallback_enabled`, `llm_intent_shadow_enabled`, `llm_intent_fallback_enabled`, and `llm_intent_min_confidence` settings with default-off behavior
- Added Gemini settings with Vertex AI defaults:
  - `gemini_model=gemini-3.1-flash-lite`
  - `gemini_project=project-gemini-0306`
  - `gemini_location=global`
  - `gemini_temperature=1.0`
  - `gemini_max_tokens=None`
  - `gemini_timeout_seconds=None`
  - `gemini_max_retries=2`
  - `gemini_vertexai=True`
- `tests/integration/conftest.py` now allows `settings_from_dsn(..., **overrides)` so mysql integration tests can force:
  - `langgraph_checkpoint_mode="mysql"`
  - `langgraph_checkpoint_setup_on_start=False`
- Added `src/app/workers/checkpoint_admin.py` with:
  - `list-runs`
  - `show-run`
  - `latest`
  - `errors`
- Added `prepare_route_state(...)` in `src/app/graph/nodes.py` so GatewayService can conservatively pre-run deterministic rewrite/router logic outside the graph
- `GatewayService` now calls `RagService.retrieve(...)` only when that pre-route result is `route == "faq"`
- SOP / human handoff / emotion care / clarification / `faq_then_sop` traffic no longer prefetches `knowledge_documents`
- `rag_node` remains a synchronous pure graph node and still falls back to static knowledge if no `rag_context` is injected
- `GraphState` now includes `llm_rewrite_result`, `llm_intent_result`, `route_source`, and `rewrite_source`
- `GatewayService` can now accept `llm_rewrite_service` / `llm_intent_service`, but shadow results are metadata only and never override deterministic rewrite/route
- `gateway_consumer` now wires the provider boundary conservatively through settings, with `llm_provider=off` as the default
- `gateway_consumer.process_next_batch(...)` now reports an `llm` summary without exposing prompts, raw model output, credentials, tokens, or secrets
- Gemini shadow output is now validated against code-side guardrails:
  - route whitelist
  - intent whitelist
  - confidence clamp to `0.0 .. 1.0`
  - stable, deduplicated risk flags
  - forced `active_workflow`, `backend_fact_like`, and `attachment_present` rewrite flags when applicable
- Added repository query methods instead of letting the CLI assemble SQL directly:
  - `GraphCheckpointRunRepository.list_runs(...)`
  - `GraphCheckpointRunRepository.get_run(...)`
  - `GraphCheckpointRunRepository.fetch_latest(...)`
  - `GraphRunErrorRepository.list_errors(...)`
- The admin CLI is read-only for `graph_checkpoint_runs` / `graph_run_errors` and never modifies LangGraph saver internal tables
- `GraphState.signal_result` has been removed
- `waiting_backend_classifier` no longer reads `state.signal_result`; it derives supplement/human-handoff signals from text and attachments directly
- `src/app/prompts/intent_router.md` now matches the post-routing-cleanup boundary and no longer mentions `signal_result`
- The FAQ-only lazy RAG transition is intentionally conservative: the full graph still re-runs rewrite/router during `graph.invoke(...)`
- The LLM boundary remains intentionally conservative:
  - Gemini is only used for rewrite shadow and intent shadow
  - Gemini never overrides deterministic `rewritten_question`, `rewrite_result`, `intent_result`, or `route`
  - Gemini never generates the final customer reply
  - Gemini never generates `external_commands`
  - fallback takeover is still not enabled
- Added a standalone `python -m app.workers.gemini_shadow_smoke --cases default --json` worker for real Vertex AI smoke review without LiveChat, outbox writes, external commands, conversation state writes, or MySQL
- `models/` reference code remains out of the MVP runtime path and was intentionally not changed
- All mysql checkpoint tests still require `MYSQL_TEST_DSN` / `DATABASE_URL` / `AI_CS_TEST_MYSQL_DSN` pointing to a disposable database whose name contains `test`
- New checkpoint persistence test bootstraps project SQL, calls real `PyMySQLSaver.setup()`, invokes a real graph, closes the provider, reopens a new provider, and verifies the same `thread_id` checkpoint can still be read
- New gateway smoke test inserts one deterministic inbound event, runs `gateway_consumer.process_next_batch(... checkpoint_mode="mysql" ...)`, verifies `conversation_states` / `conversation_messages` / `outbound_messages` / `graph_checkpoint_runs`, then reopens a provider and verifies checkpoint readability again
- New checkpoint admin mysql smoke test inserts one `graph_checkpoint_runs` row plus one `graph_run_errors` row into a disposable test schema, then verifies `list-runs` / `show-run` / `latest` / `errors` all return JSON-ready data
- `tests/integration/conftest.py` now provisions a fresh per-test MySQL schema whose name still contains `test`, bootstraps it, and drops it after the run
- The provisioned integration schema uses `utf8mb4_0900_ai_ci` on this MySQL 8.4 machine to avoid saver collation mismatches during real checkpoint reads
- Added `scripts/setup_mysql_test_db.sh` to create the safe local base database `ai_customer_service_test` without writing credentials to Git

## Latest Verification Status

- Ran `uv run --group dev pytest tests/unit -q`
- Result: `268 passed`
- Ran `MYSQL_TEST_DSN='mysql+pymysql://root:lingxi%40123@127.0.0.1:3306/ai_customer_service_test' PYTHONPATH=src uv run --group dev pytest tests/integration -m mysql -q`
- Result: `6 passed`
- Ran `MYSQL_TEST_DSN='mysql+pymysql://root:lingxi%40123@127.0.0.1:3306/ai_customer_service_test' PYTHONPATH=src uv run --group dev pytest tests/integration -m mysql -q`
- Result: `6 passed`
- Created local base database: `ai_customer_service_test`
- DSN env used for verification: `MYSQL_TEST_DSN`
- Ran `PYTHONPATH=src uv run --group dev pytest tests/integration/test_mysql_checkpoint_persistence.py -q`
- Result: `1 passed`
- Ran `PYTHONPATH=src uv run --group dev pytest tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py -q`
- Result: `1 passed`
- Ran `PYTHONPATH=src uv run --group dev pytest tests/integration/test_checkpoint_admin_mysql_smoke.py -q`
- Result: `1 passed`
- Ran `PYTHONPATH=src uv run --group dev pytest tests/integration -m mysql -q`
- Result: `6 passed`
- No mysql integration test was run against `livechat_ai`; test provisioning remains locked to database names containing `test`

## Recommended Commands For A Prepared MySQL Test DB

```bash
chmod +x scripts/setup_mysql_test_db.sh
./scripts/setup_mysql_test_db.sh

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_mysql_checkpoint_persistence.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_checkpoint_admin_mysql_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration -m mysql -q
```

If the password contains special characters, URL-encode it in the DSN. A true local verification result must print `passed`; `skipped` means the DSN was not loaded or failed the safety checks.

## Current Polling-First Worker Status

This session hardened the polling-first worker path without adding websocket or webhook ingress.
P0 ingress contract is done, and P1-A added graph failure isolation with `graph_run_errors`.
P2 added `conversation_messages` for conversation history, P3-A added LangGraph `thread_id = conversation_id` config plus checkpointer injection support, P3-B added the checkpoint provider boundary, P4-A replaced normal RAG placeholder behavior with deterministic KB-backed replies, and P4-B wired DB-backed `knowledge_documents` retrieval into GatewayService via RagService.

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
- `graph_run_errors` persistence for graph invoke failures before any outbox, external command, or conversation state side effect
- `gateway_consumer` per-event failure isolation with `processed` / `failed` / `enqueued` summary output
- `conversation_messages` for customer, assistant, and external summary history
- LangGraph invoke config uses `configurable.thread_id = conversation_id`
- `build_workflow_graph(checkpointer=...)` supports injecting a checkpointer without creating one internally
- `LANGGRAPH_CHECKPOINT_MODE=off|memory|mysql` is supported by the provider boundary
- `mysql` now uses `PyMySQLSaver` from `langgraph-checkpoint-mysql[pymysql]`
- `python -m app.workers.setup_langgraph_checkpoints` runs saver `.setup()` explicitly
- read-only graph debug helpers can fetch latest state and state history by `conversation_id`
- `sql/011_graph_checkpoint_metadata.sql` adds project-owned `graph_checkpoint_runs` metadata
- `GraphCheckpointRunRepository` records checkpoint-mode run metadata without replacing `graph_run_errors`
- `gateway_consumer` now creates and injects `GraphCheckpointRunRepository(pool)` through the existing provider boundary
- `gateway_consumer` keeps the MySQL checkpointer alive for the whole batch and closes it afterward
- `python -m app.workers.checkpoint_admin list-runs|show-run|latest|errors ...` provides a read-only JSON admin surface for checkpoint metadata and graph errors
- `knowledge_documents` stores tenant/kb-scope KB documents for deterministic retrieval
- `gateway_consumer` creates `KnowledgeDocumentRepository(pool)` and `RagService(...)`
- `GatewayService` pre-runs deterministic rewrite/router logic and only prefetches `rag_context` for `route=faq`
- `gateway_consumer` now also creates a model-provider boundary; current supported modes are only `off` and `mock`
- mock rewrite shadow records `llm_rewrite_result` without changing `rewritten_question`
- mock intent shadow records `llm_intent_result` without changing `intent_result` or `route`
- `rag_node` reads `rag_context` synchronously and never opens DB connections
- normal FAQ/RAG path produces only customer-facing `livechat.send_text`
- normal FAQ/RAG path no longer emits `rag.placeholder` external commands
- `python -m app.workers.seed_knowledge --tenant-id default --kb-scope default` seeds default static FAQ documents idempotently
- `python -m app.workers.seed_knowledge --tenant-id default --kb-scope default --source-file <path>` supports JSON file seeding with skip counts for invalid documents
- `python -m app.workers.knowledge_admin list|get|enable|disable ...` provides lightweight KB administration via repository methods only

Ingress staging remains:

- 前期: Polling 主入口。
- 中期: WebSocket 主入口，Polling 用于断线补偿、指定 chat 补拉、本地调试。
- 后期: Webhook 正式主入口，Polling 用于 targeted fallback、异常恢复、排障、数据校验。

TODO for later phases only:

- WebSocket RTM receiver
- Webhook receiver and signature verification
- webhook registration docs
- interrupt/resume
- checkpoint debug/admin Web UI
- real LLM provider integration
- LLM tool calling
- vector RAG, embeddings, and LLM answer generation
- knowledge-base web management UI
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
