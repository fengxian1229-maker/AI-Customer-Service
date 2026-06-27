AI Customer Service MVP
=======================

Polling-first LiveChat/Text.com customer-service MVP.

Current scope:

- Poll LiveChat Agent Chat API for allowed groups.
- Normalize customer `message` and `file` events.
- Store events in MySQL `inbound_events`.
- Consume inbound events into `conversation_states`, `conversation_messages`, and LangGraph.
- Send pending text replies through LiveChat `send_event`.

Ingress Direction
-----------------

The current LiveChat polling path is an early-stage fallback ingress. It is intentionally light: it lists chats, fetches chat details when permitted, filters allowed groups and agent/self messages, normalizes supported events, and writes `inbound_events`.

The planned ingress stages are:

- Early stage: polling fallback receiver for local smoke tests and bounded fallback intake.
- Mid stage: WebSocket realtime ingress.
- Production stage: webhook ingress.

All ingress implementations must normalize into the same `inbound_events` structure through the shared ingress contract. The downstream path starts at `GatewayConsumer`, so `GatewayConsumer -> conversation_states / outbound_messages / external_commands -> workers` must not change when the ingress source changes.

Gateway processing now uses this main path:

```text
inbound_events
  -> gateway_consumer
  -> conversation_states
  -> conversation_messages
  -> LangGraph with config.thread_id = conversation_id
  -> outbound_messages / external_commands
```

P3-A has introduced the LangGraph checkpointer injection boundary and per-conversation thread config. Durable checkpoint storage plus interrupt/resume are later work.

Checkpoint modes:

- `off`: default, no checkpointer.
- `memory`: local/dev/test only, uses LangGraph `InMemorySaver` and is not durable.
- `mysql`: explicit durable mode for LangGraph MySQL checkpoints. It must be configured intentionally and should only be used after saver setup succeeds.

P3-B adds a checkpoint provider boundary and read-only graph debug helpers. P5-A adds durable checkpoint design, a checkpoint metadata schema, and a provider boundary that explicitly recognizes `off`, `memory`, and planned `mysql` modes. P5-A.1 wires checkpoint run metadata through `gateway_consumer -> GatewayService` using `GraphCheckpointRunRepository`. P5-B adds `langgraph-checkpoint-mysql[pymysql]`, a real `PyMySQLSaver` provider path for `LANGGRAPH_CHECKPOINT_MODE=mysql`, and an explicit setup worker for saver-managed internal tables.

P4-A adds minimal deterministic knowledge-base-backed RAG. P4-B connects `knowledge_documents` retrieval into the Gateway/RAG path through `KnowledgeDocumentRepository` and `RagService` injection. P4-C adds tenant/kb-scope knowledge management plus deterministic ranking v1. Normal FAQ/RAG answers now produce a customer-facing `livechat.send_text` reply and do not emit `external_commands`. RAG remains read-only and must not answer backend, payment, withdrawal, account, balance, turnover, or order facts. P5-C adds a read-only checkpoint admin CLI for `graph_checkpoint_runs` and `graph_run_errors`; it is for debugging only and does not modify LangGraph saver tables. P5-D now tightens RAG retrieval so only FAQ traffic prefetches DB-backed `rag_context` before the full graph invoke. P6-A adds a model-provider boundary with mock rewrite shadow and mock intent shadow, both default-off and non-authoritative. P6-B adds a real Gemini Vertex AI shadow provider through `langchain-google-genai` `ChatGoogleGenerativeAI`. P6-B.1 adds Gemini shadow output guardrails and a standalone smoke review worker. P7-A.1 adds a multimodal, vector-ready FAQ canonical data layer on `knowledge_documents` with `question_aliases`, `answer_blocks`, and `metadata_json`; retrieval is still lexical and Gateway output remains single text. P7-A.3 adds a read-only FAQ `answer_blocks` renderer preview helper; it is pure, does not write outbox rows, and does not send images. P7-A.4 adds a read-only FAQ multi-outbound dry-run planner; it returns future outbound plan structures without writing or sending them. P7-A.5 prepares `outbound_messages` for future multi-outbound idempotency with nullable `dedup_key`, `block_index`, `message_kind`, and `command_type` fields while keeping the current single-text path unchanged. P7-A.7 hardens the FAQ single-text closed-loop smoke path with a sender pending SQL ambiguity regression fix, fake-sender MySQL smoke coverage, and a read-only `faq_smoke_admin` diagnostics CLI. P7-A.8 hardens LLM rewrite/intent shadow inside the Gateway path: shadow success/error summaries are recorded in checkpoint metadata, shadow failures do not block deterministic FAQ single-text output, and `llm_shadow_admin` provides read-only diagnostics.

Current RAG limits:

- No vector database.
- No embeddings.
- No LLM answer generation.
- No LLM tool calling.
- No knowledge-base web admin UI.
- No production FAQ outbound renderer or image sending.
- A read-only FAQ renderer preview exists, but Gateway output remains single text.
- A read-only FAQ multi-outbound dry-run planner exists, but it does not write `outbound_messages`.
- `outbound_messages` has future multi-block idempotency fields, but Gateway does not populate FAQ multi-block rows yet.
- FAQ single-text can be repeatedly smoke-tested and diagnosed, but production FAQ multi-image/buttons sending is still not connected.
- No real backend or Telegram calls.
- DB-backed RAG retrieval is prefetched only for deterministic `route=faq`.
- SOP, human handoff, emotion care, clarification, and `faq_then_sop` traffic do not prefetch `knowledge_documents`.
- Backend-fact questions may still enter RagService guardrail handling, but they do not query `knowledge_documents` and still return a safe fallback.
- Normal RAG path never emits `RAG_PLACEHOLDER` and never writes `external_commands`.

Current LLM boundary:

- `llm_provider` supports `off`, `mock`, and `gemini`.
- Default runtime is `llm_provider=off`.
- Gemini uses Vertex AI through `ChatGoogleGenerativeAI` with:
  - `model=gemini-3.1-flash-lite`
  - `project=project-gemini-0306`
  - `location=global`
  - `vertexai=True`
- Mock rewrite shadow records `llm_rewrite_result` but never overrides deterministic `rewritten_question`.
- Mock intent shadow records `llm_intent_result` but never overrides deterministic `intent_result` or `route`.
- Gemini rewrite shadow records only `llm_rewrite_result` and never overrides deterministic `rewritten_question` or `rewrite_result`.
- Gemini intent shadow records only `llm_intent_result` and never overrides deterministic `intent_result` or `route`.
- Gemini shadow output is normalized by code-side guardrails for route, intent, confidence, and risk flags.
- Gateway-path shadow result/error summaries are stored in `graph_checkpoint_runs.metadata_json.llm_shadow`.
- Shadow failures are isolated from deterministic graph execution and do not create `graph_run_errors`.
- Gemini is not used for final customer reply generation.
- Gemini does not call third-party APIs or generate `external_commands`.
- The full graph still re-runs rewrite/router on invoke, so the real Gemini call is kept outside graph nodes.
- `models/` reference code is not part of the current MVP runtime boundary and is not used by the active provider path.
- Third-party actions still must go through deterministic `external_commands` plus workers; the LLM boundary does not call external APIs directly.

Seed default knowledge documents:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.seed_knowledge --tenant-id default --kb-scope default
```

Preview seed documents without writing:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.seed_knowledge --tenant-id default --kb-scope default --dry-run
```

Seed from a JSON file or disable documents at seed time:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.seed_knowledge --tenant-id default --kb-scope default --source-file ./knowledge.json --enabled false --limit 10
```

Seed the minimal multimodal FAQ canonical data set:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.seed_knowledge --tenant-id default --kb-scope default --source-file data/knowledge/default_multimodal_faq_seed.json
```

FAQ renderer preview helper:

```python
from app.services.faq_renderer import render_answer_blocks_preview

preview = render_answer_blocks_preview(answer_blocks, platform="JUE999", channel_type="livechat", language="zh")
```

This preview helper only returns internal `text` / `image` / `buttons` preview blocks. It does not write `outbound_messages`, does not call `sender_worker`, does not check whether asset files exist, and does not upload or send images.

FAQ multi-outbound dry-run planner:

```python
from app.services.faq_outbound_plan import build_faq_outbound_plan

plan = build_faq_outbound_plan(
    answer_blocks=answer_blocks,
    tenant_id="default",
    conversation_id="livechat:chat-1",
    inbound_event_id="event-1",
)
```

This planner returns deterministic dry-run message plans for future `text` / `image` / `buttons` outbound rendering. It does not write `outbound_messages`, does not call `sender_worker`, and does not upload or send images.

FAQ single-text smoke diagnostics:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.faq_smoke_admin summary --limit 20
PYTHONPATH=src uv run --group dev python -m app.workers.faq_smoke_admin latest-inbound --limit 5
PYTHONPATH=src uv run --group dev python -m app.workers.faq_smoke_admin latest-outbound --limit 5
PYTHONPATH=src uv run --group dev python -m app.workers.faq_smoke_admin latest-conversation --limit 10
PYTHONPATH=src uv run --group dev python -m app.workers.faq_smoke_admin latest-checkpoints --limit 5
PYTHONPATH=src uv run --group dev python -m app.workers.faq_smoke_admin latest-errors --limit 5
```

See [docs/p7-a7-faq-single-text-closed-loop-smoke.md](/Users/andy/ai-agent/docs/p7-a7-faq-single-text-closed-loop-smoke.md). The diagnostics are read-only, output JSON with Chinese text preserved, and do not query LangGraph saver internal tables.

Lightweight knowledge admin CLI:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.knowledge_admin list --tenant-id default --kb-scope default
PYTHONPATH=src uv run --group dev python -m app.workers.knowledge_admin get --tenant-id default --kb-scope default --title "奖金规则说明"
PYTHONPATH=src uv run --group dev python -m app.workers.knowledge_admin disable --tenant-id default --kb-scope default --title "奖金规则说明"
PYTHONPATH=src uv run --group dev python -m app.workers.knowledge_admin enable --tenant-id default --kb-scope default --title "奖金规则说明"
```

Current receiver boundaries:

- Polling does not call LangGraph.
- Polling does not run SOP, RAG, backend lookup, Telegram handoff, or sender logic.
- Polling does not implement worker lease, production scheduling, or cursor tables.
- `--groups` or `LIVECHAT_ALLOWED_GROUP_IDS` must be explicit.

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

To enable Gemini Vertex AI shadow mode:

```env
LLM_PROVIDER=gemini
LLM_REWRITE_SHADOW_ENABLED=true
LLM_INTENT_SHADOW_ENABLED=true
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_PROJECT=project-gemini-0306
GEMINI_LOCATION=global
GEMINI_TEMPERATURE=1.0
GEMINI_MAX_RETRIES=2
GEMINI_VERTEXAI=true
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

To run the standalone Gemini shadow smoke review without LiveChat, outbox writes, or gateway processing:

```bash
PYTHONPATH=src \
LLM_PROVIDER=gemini \
LLM_REWRITE_SHADOW_ENABLED=true \
LLM_INTENT_SHADOW_ENABLED=true \
python -m app.workers.gemini_shadow_smoke --cases default --json
```

Read Gateway-path shadow diagnostics:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.llm_shadow_admin latest --conversation-id livechat:<chat-id> --limit 5
PYTHONPATH=src uv run --group dev python -m app.workers.llm_shadow_admin summary --conversation-id livechat:<chat-id> --limit 20
```

See [docs/p7-a8-llm-shadow-gateway-smoke.md](/Users/andy/ai-agent/docs/p7-a8-llm-shadow-gateway-smoke.md). The admin reads project-owned metadata only and never reads LangGraph saver internal tables.

Run Tests
---------

```bash
uv run --group dev pytest tests/unit -v
```

Create the safe local base database for MySQL integration tests:

```bash
chmod +x scripts/setup_mysql_test_db.sh
./scripts/setup_mysql_test_db.sh
```

The script only creates `ai_customer_service_test`. It does not drop databases or truncate tables.

MySQL checkpoint integration checks require a disposable database whose name contains `test`:

```bash
MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_mysql_checkpoint_persistence.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_checkpoint_admin_mysql_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_faq_single_text_closed_loop_mysql_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_llm_shadow_gateway_mysql_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration -m mysql -q
```

Notes for local MySQL integration:

- If the password contains special characters, URL-encode it in `MYSQL_TEST_DSN`.
- The checked-in integration helpers provision a fresh per-test schema whose name still contains `test`, then drop that schema after the test run.
- A real pass result looks like `1 passed` / `5 passed`; `skipped` means the DSN was not picked up or failed the safety checks.
- Verified on this machine on `2026-06-27`:
  - `tests/integration/test_mysql_checkpoint_persistence.py -q` -> `1 passed`
  - `tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py -q` -> `1 passed`
  - `tests/integration/test_checkpoint_admin_mysql_smoke.py -q` -> `1 passed`
  - `tests/integration/test_faq_single_text_closed_loop_mysql_smoke.py -q` -> `2 passed`
  - `tests/integration/test_llm_shadow_gateway_mysql_smoke.py -q` -> `2 passed`
  - `tests/integration -m mysql -q` -> `10 passed`

Bootstrap Database
------------------

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.bootstrap_db
```

Durable checkpoint design:

- [docs/durable-checkpoint-storage-design.md](/Users/andy/ai-agent/docs/durable-checkpoint-storage-design.md)
- `graph_checkpoint_runs` is metadata-only and does not replace `conversation_states`, `conversation_messages`, or `graph_run_errors`
- `gateway_consumer` now creates and injects `GraphCheckpointRunRepository(pool)` for lightweight checkpoint-run metadata auditing
- LangGraph saver internal tables are created by saver `.setup()`, not by handwritten project SQL

FAQ-only lazy RAG boundary:

- `GatewayService` remains the DB-backed RAG retrieve boundary.
- `rag_node` remains a synchronous pure graph node.
- `GatewayService` now pre-runs deterministic rewrite/router logic and only calls `RagService.retrieve(...)` when the pre-route result is `faq`.
- This conservative transition keeps the existing LangGraph topology unchanged; the full graph still re-runs rewrite/router on invoke.

Set up LangGraph MySQL checkpoint tables explicitly:

```bash
PYTHONPATH=src LANGGRAPH_CHECKPOINT_MODE=mysql uv run --group dev python -m app.workers.setup_langgraph_checkpoints
```

Read-only checkpoint admin CLI:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.checkpoint_admin list-runs --conversation-id livechat:chat-1
PYTHONPATH=src uv run --group dev python -m app.workers.checkpoint_admin show-run --run-id 123
PYTHONPATH=src uv run --group dev python -m app.workers.checkpoint_admin latest --conversation-id livechat:chat-1
PYTHONPATH=src uv run --group dev python -m app.workers.checkpoint_admin errors --conversation-id livechat:chat-1
```

Supported filters:

- `--conversation-id`
- `--graph-thread-id`
- `--inbound-event-id`
- `--created-after`
- `--created-before`
- `--status` for checkpoint-run queries
- `--limit` for list commands

Run Gateway with durable MySQL checkpoints:

```bash
PYTHONPATH=src LANGGRAPH_CHECKPOINT_MODE=mysql uv run --group dev python -m app.workers.gateway_consumer --once --limit 20
```

Poll LiveChat Once
------------------

```bash
PYTHONPATH=src LIVECHAT_ALLOWED_GROUP_IDS=23 uv run --group dev python -m app.workers.polling_receiver --once --groups 23 --limit 20
```

Run Polling Fallback Loop
-------------------------

```bash
PYTHONPATH=src LIVECHAT_ALLOWED_GROUP_IDS=23 uv run --group dev python -m app.workers.polling_receiver --groups 23 --limit 20 --sleep-seconds 5
```

For bounded local tests:

```bash
PYTHONPATH=src LIVECHAT_ALLOWED_GROUP_IDS=23 uv run --group dev python -m app.workers.polling_receiver --groups 23 --limit 20 --sleep-seconds 1 --max-iterations 2
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
- Polling-first remains the only ingress in this stage. WebSocket/Webhook are later phases.
- `LANGGRAPH_CHECKPOINT_MODE=off` remains the default runtime recommendation.
- `LANGGRAPH_CHECKPOINT_MODE=memory` is only for local/dev/test.
- `LANGGRAPH_CHECKPOINT_MODE=mysql` requires `langgraph-checkpoint-mysql[pymysql]`, successful saver setup, and a MySQL server version supported by the upstream saver.
- `mysql_checkpoint_dsn` uses `mysql://user:password@host:port/database?charset=utf8mb4` with the password URL-encoded.
- This project uses `PyMySQLSaver` for sync `graph.invoke(...)`; it does not switch GatewayService to async graph invocation in P5-B.
- Interrupt/resume, WebSocket/Webhook, vector DB, embedding, LLM final answer generation, LLM tool calling, and real Telegram/backend integration remain out of scope.
