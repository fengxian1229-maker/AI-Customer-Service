# P7-A.8 LLM Shadow Gateway Smoke

P7-A.8 only validates LLM rewrite / intent shadow inside the existing `gateway_consumer` path. It does not enable fallback, does not generate final customer replies, does not change the real route, and does not let LLM decide images or buttons.

## Boundary

- LLM shadow result is metadata only.
- Deterministic rewrite/router remains authoritative.
- Deterministic FAQ/RAG still produces the real `livechat.send_text` outbound.
- `llm_rewrite_fallback_enabled=false` and `llm_intent_fallback_enabled=false` remain the expected setting.
- Shadow failures are captured as sanitized shadow error summaries and do not block deterministic FAQ single-text output.
- Shadow-only failures do not write `graph_run_errors`; real deterministic graph failures still do.

Current non-goals:

- LLM final answer generation
- LLM fallback
- LLM tool calling
- embedding/vector DB
- FAQ multi-image production send
- LiveChat `send_image`
- buttons/rich message
- WebSocket/Webhook

## Gateway-Path Fake/Mock Smoke

Run the MySQL integration smoke with a disposable test DB:

```bash
MYSQL_TEST_DSN='mysql+pymysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src \
uv run --group dev pytest tests/integration/test_llm_shadow_gateway_mysql_smoke.py -q
```

This test inserts “怎么存款？”, runs `gateway_consumer.process_next_batch(...)`, uses mock/fake LLM providers, and verifies:

- inbound event is processed
- customer `conversation_messages` row is written
- exactly one text `outbound_messages` row is created as `PENDING`
- outbound text still comes from deterministic FAQ/RAG
- `graph_checkpoint_runs.status=SUCCEEDED`
- `graph_checkpoint_runs.metadata_json.llm_shadow` contains shadow result or error summaries
- `graph_run_errors` remains empty for shadow-only failures

## Standalone Gemini Smoke

The standalone Gemini smoke still does not touch LiveChat, outbox, conversation state, or MySQL:

```bash
PYTHONPATH=src \
LLM_PROVIDER=gemini \
LLM_REWRITE_SHADOW_ENABLED=true \
LLM_INTENT_SHADOW_ENABLED=true \
uv run --group dev python -m app.workers.gemini_shadow_smoke --cases default --json
```

This is useful for real Vertex AI structured-output review, but it is not a Gateway-path smoke. Use the MySQL smoke above to verify the production Gateway boundary.

## Diagnostics

Use the read-only LLM shadow admin:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.llm_shadow_admin latest \
  --conversation-id livechat:<chat-id> \
  --limit 5

PYTHONPATH=src uv run --group dev python -m app.workers.llm_shadow_admin summary \
  --conversation-id livechat:<chat-id> \
  --limit 20
```

The CLI reads only project-owned `graph_checkpoint_runs` metadata through repository methods. It does not query LangGraph saver internal tables, does not modify tables, and does not output full payloads or secrets.

Expected shadow metadata shape:

```json
{
  "llm_shadow": {
    "rewrite": {
      "provider": "mock",
      "mode": "shadow",
      "status": "ok"
    },
    "intent": {
      "provider": "mock",
      "mode": "shadow",
      "status": "ok",
      "route": "faq"
    },
    "deterministic_route": "faq",
    "deterministic_intent": "deposit_howto"
  }
}
```

If a shadow call fails, the result is intentionally short and sanitized:

```json
{
  "mode": "shadow",
  "status": "error",
  "error_type": "RuntimeError"
}
```
