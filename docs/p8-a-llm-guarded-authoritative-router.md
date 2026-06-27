# P8-A LLM Guarded Authoritative Router

P8-A adds an optional LLM router mode that can authoritatively set only the rewritten question and route metadata before the graph invoke. It is still bounded by deterministic safety logic and never generates the final customer reply.

## Runtime Flags

Defaults remain conservative:

```env
LLM_ROUTER_MODE=shadow
LLM_ROUTER_MIN_CONFIDENCE=0.75
LLM_ROUTER_FALLBACK_TO_DETERMINISTIC=true
```

Supported `LLM_ROUTER_MODE` values:

- `deterministic`: do not call the router LLM.
- `shadow`: keep the P7-A.8 shadow-only behavior.
- `guarded_authoritative`: allow accepted LLM router decisions to override `rewritten_question`, `rewrite_result`, `intent_result`, and `route`.

## Guardrails

The LLM router decision is accepted only when all checks pass:

- schema validation succeeds
- `route` is in the route whitelist
- `intent` is in the intent whitelist
- `confidence >= LLM_ROUTER_MIN_CONFIDENCE`
- FAQ decisions are rejected for backend/account/order/payment/balance/status fact-like requests
- human-required decisions must use `human_handoff`
- active workflows, file-without-text events, explicit human requests, and FAQ-leaning backend fact-like traffic use deterministic hard guards

Accepted decisions set:

- `rewrite_source=llm_guarded_authoritative`
- `route_source=llm_guarded_authoritative`
- `llm_router_result.status=accepted`

Fallback decisions keep deterministic routing and set:

- `llm_router_result.status=fallback`
- `llm_router_result.fallback_reason`
- optional `llm_router_result.hard_guard`

## Non-Goals

P8-A still does not implement:

- LLM final answer generation
- LLM tool calling
- LLM-created `external_commands`
- LLM decisions for FAQ image/buttons/multi-outbound rendering
- WebSocket/Webhook ingress
- vector DB or embeddings
- real backend facts

Final customer replies still come from deterministic graph nodes, RAG/static knowledge, SOP handlers, or handoff/clarification nodes.

## Diagnostics

Gateway checkpoint metadata now includes `metadata_json.llm_router` when the router mode produces an accepted or fallback result.

Read-only diagnostics:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.llm_shadow_admin latest --conversation-id livechat:<chat-id> --limit 5
PYTHONPATH=src uv run --group dev python -m app.workers.llm_shadow_admin summary --conversation-id livechat:<chat-id> --limit 20
```

The admin reads project-owned `graph_checkpoint_runs.metadata_json` only. It does not read LangGraph saver internal tables and it sanitizes secret-like fields.

## Verification

Unit:

```bash
uv run --group dev pytest tests/unit/test_gateway.py tests/unit/test_gateway_consumer.py tests/unit/test_worker_cli.py -q
```

MySQL smoke:

```bash
MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_llm_guarded_authoritative_router_mysql_smoke.py -q
```

The MySQL test provisions an isolated schema whose name contains `test`, then verifies FAQ acceptance, SOP acceptance, low-confidence fallback, active-workflow hard guard, checkpoint metadata, and no `graph_run_errors`.
