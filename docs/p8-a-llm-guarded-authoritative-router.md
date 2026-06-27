# P8-A LLM Guarded Authoritative Router

P8-A adds an optional LLM router mode that can authoritatively set only the rewritten question and route metadata before the graph invoke. It is still bounded by deterministic safety logic and never generates the final customer reply.

## Runtime Flags

Defaults remain conservative:

```env
LLM_ROUTER_MODE=shadow
LLM_ROUTER_MIN_CONFIDENCE=0.75
LLM_ROUTER_FALLBACK_TO_DETERMINISTIC=true
```

`LLM_ROUTER_FALLBACK_TO_DETERMINISTIC` is retained as a config and diagnostics field in P8-A, but safety fallback is not disableable: rejected, low-confidence, invalid, or hard-guarded LLM router decisions always continue through deterministic routing.

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
- FAQ decisions are rejected for backend/account/order/payment/balance/status fact-like requests, including Spanish deposit/withdrawal missing-status phrasing such as `mi deposito no llegó`
- human-required decisions must use `human_handoff`
- active workflows, file-without-text events, explicit human requests, deterministic SOP / `faq_then_sop` routes, deterministic human/emotion routes, and FAQ-leaning backend fact-like traffic use deterministic hard guards

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

The MySQL test provisions an isolated schema whose name contains `test`, then verifies FAQ acceptance, deterministic SOP hard guards for Chinese and Spanish deposit-missing cases, low-confidence fallback, active-workflow hard guard, checkpoint metadata, and no `graph_run_errors`.

## P8-A.2 Real Gemini Dry-Run Review

`python -m app.workers.real_gemini_guarded_smoke` runs a dry-run-only real Gemini `guarded_authoritative` small-sample review.

Default cases:

- `faq_deposit_howto_zh`: `怎么存款？`, expects final FAQ route and no external commands.
- `faq_withdrawal_howto_en`: `how to withdraw?`, expects final FAQ route and no external commands.
- `sop_deposit_missing_es`: `mi deposito no llegó`, forbids final FAQ route.
- `explicit_human_en`: `I want a human agent`, forbids final FAQ route.
- `backend_fact_balance_en`: `what is my balance?`, forbids final FAQ route.
- `backend_fact_order_status_zh`: `我的订单现在是什么状态？`, forbids final FAQ route.
- `file_without_text`: `FILE_RECEIVED` with empty text, forbids final FAQ route and accepted router takeover.

The CLI always uses generated fake chat/thread ids, calls `gateway_consumer.process_inbound_event_id`, reads diagnostics by `(conversation_id, inbound_event_id)`, marks pending dry-run outbounds as `SKIPPED_MANUAL_SMOKE`, and never sends LiveChat.

P8-A.2.1 tightens dry-run safety:

- `explicit_human_en` may produce `human_handoff.requested`.
- backend fact-like cases may produce `human_handoff.requested` or `backend.query`.
- FAQ and file-without-text cases still require zero external commands.
- pending `external_commands` for this inbound event are marked `SKIPPED_MANUAL_SMOKE` and are never executed.
- unsupported `--case-set`, unknown `--case`, and non-positive `--limit` return JSON errors before pool creation or inbound insertion.

```bash
LLM_PROVIDER=gemini \
LLM_ROUTER_MODE=guarded_authoritative \
LLM_ROUTER_MIN_CONFIDENCE=0.75 \
GEMINI_MODEL=gemini-3.1-flash-lite \
GEMINI_VERTEXAI=true \
GEMINI_PROJECT=project-gemini-0306 \
GEMINI_LOCATION=global \
PYTHONPATH=src \
uv run --group dev python -m app.workers.real_gemini_guarded_smoke \
  --case-set default \
  --seed-default-faq
```
