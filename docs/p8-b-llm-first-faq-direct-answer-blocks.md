# P8-B LLM-First FAQ Direct Answer Blocks

P8-B adds a smoke-test mode for FAQ only:

```env
LLM_ROUTER_MODE=faq_authoritative
```

In this mode, ordinary `MESSAGE_CREATED` text is routed as:

```text
user message
-> LLM rewrite / route / faq_query
-> FAQ retrieval with faq_query / normalized_query
-> knowledge_documents.answer_blocks
-> ordered outbound_messages
-> sender_worker text / image fallback / buttons preview
```

## LLM Boundary

The LLM is only allowed to decide rewrite/router metadata:

- `rewritten_question`
- `normalized_query`
- `language`
- `intent`
- `route`
- `faq_query`
- `confidence`
- `reason`

It does not generate final customer replies, images, buttons, tool calls, or `external_commands`.

In `faq_authoritative`, the router payload keeps deterministic fields as `None`:

- `deterministic_rewrite_result=None`
- `deterministic_intent_result=None`
- `deterministic_route=None`

Provider missing, invalid schema, low confidence, or non-FAQ decisions fall back to deterministic-free clarification, not keyword reclassification.

## P8-B.1 Hardening

P8-B.1 formalizes issues found during manual real Gemini FAQ-authoritative smoke:

- Gemini has separate router prompts for `guarded_authoritative` and `faq_authoritative`.
- `ROUTER_SYSTEM_PROMPT` remains as a backward-compatible alias to the guarded prompt, not the FAQ prompt.
- Gateway sends `router_mode` and `mode` in router payloads.
- Gemini and mock providers return `mode` equal to the requested router mode.
- route and intent guardrails normalize common model aliases instead of expanding internal canonical enums.
- `faq_authoritative` active workflow, invalid route, low confidence, missing provider, validation error, file-without-text, and empty input all fall back to deterministic-free clarification with `fallback_to_deterministic=false`.
- Router checkpoint metadata retains compact `reason`, rewrite/query fields, `faq_query`, `language`, errors, final route/source fields, and compact RAG retrieval diagnostics.
- `llm_shadow_admin` can JSON dump datetime values and continues to sanitize secret-like fields.

## P8-B.2 Scoped Smoke Safety

P8-B.2 locks the real Gemini FAQ smoke to the single inbound event it creates:

- `InboundEventRepository.fetch_unprocessed_by_id()` reads only the requested unprocessed, non-ignored inbound event.
- `gateway_consumer.process_inbound_event_id()` processes only that inbound event and returns `not_found=true` instead of falling back to a batch.
- `OutboundMessageRepository.fetch_pending_by_inbound_event()` reads only pending outbound rows for that inbound event.
- `sender_worker.process_pending_for_inbound_event()` sends only pending rows for that inbound event.
- `real_gemini_faq_smoke` default no-send mode creates `Settings` with unused LiveChat credentials, so it does not require real LiveChat secrets.
- `--send` now requires explicit `--chat-id` and `--thread-id` before any inbound row is inserted.
- Default no-send skip still marks pending rows `SKIPPED_MANUAL_SMOKE`, but only where `outbound_messages.inbound_event_id` equals the smoke event id.
- Gateway LLM error metadata redacts secret values such as `api_key=...`, `password: ...`, `token=...`, and `Bearer ...`, not only key names.

## P8-B.2.1 Smoke Completeness

P8-B.2.1 tightens send-mode diagnostics without changing the default no-send behavior:

- `--send` success requires `pending_before_count > 0`.
- `sender_result_count` must equal `pending_before_count`.
- Every sender result must carry this smoke's `inbound_event_id`.
- Sender statuses must be limited to `SENT` and `SKIPPED_PREVIEW`.
- `pending_after_count` must be `0`.
- `SKIPPED_PREVIEW` is allowed for buttons preview, but the warning states `buttons preview was skipped by sender_worker`.
- If `--sender-limit` leaves rows pending, `smoke_success=false` and warning includes `sender_limit_may_have_left_pending_outbounds`.
- Router metadata and graph errors are fetched by `(conversation_id, inbound_event_id)` so historical rows for the same chat do not affect the current smoke.

## FAQ Retrieval

FAQ retrieval query priority is:

1. `intent_result.faq_query`
2. `rewrite_result.normalized_query`
3. `rewritten_question`
4. `raw_user_input`

For this FAQ-first smoke mode, backend-fact RAG guard can be disabled by state so a phrase like `deposit not arrived FAQ` can still retrieve an explicitly seeded FAQ document. This is limited to `faq_authoritative` retrieval and does not enable backend fact answering.

## Outbound Blocks

When `route=faq` and `rag_context.answer_blocks` exists, Gateway renders answer blocks into multiple `outbound_messages` and suppresses the default single FAQ text command to avoid duplicates.

Supported block behavior:

- `text`: `command_type=livechat.send_text`, `message_type=text`
- `image`: `command_type=livechat.send_image`, `message_type=image`
- `buttons`: `command_type=livechat.buttons_preview`, `message_type=buttons`

Rows keep stable `dedup_key`, `block_index`, `message_kind`, and `command_type`.

The legacy `(inbound_event_id, action_type)` unique key is no longer kept because one FAQ answer can legitimately create multiple blocks with the same command type. Multi-block idempotency is based on `dedup_key`.

## Sender Worker

`sender_worker` now dispatches by `message_type`:

- `text`: sends through `send_text`.
- `image`: sends an MVP fallback text containing the image URL and caption, and marks the row `SENT`.
- `buttons`: marks the row `SKIPPED_PREVIEW`.
- unknown types: mark `SKIPPED_UNSUPPORTED`.

The current image/buttons behavior is not production-grade LiveChat rich messaging.

## Still Not Implemented

- LLM final answer generation
- LLM tool calling
- LLM-created `external_commands`
- real backend / Tiancheng calls
- real Telegram
- WebSocket / Webhook ingress
- embeddings / vector DB
- real LiveChat image upload
- real LiveChat rich buttons

## Verification

Unit:

```bash
uv run --group dev pytest tests/unit -q
```

MySQL smoke:

```bash
MYSQL_TEST_DSN='mysql+pymysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src \
uv run --group dev pytest tests/integration/test_llm_faq_authoritative_multimodal_mysql_smoke.py -q
```

The MySQL smoke is skipped when no MySQL test DSN is configured.

Manual real Gemini smoke:

```bash
LLM_PROVIDER=gemini \
LLM_ROUTER_MODE=faq_authoritative \
LLM_ROUTER_MIN_CONFIDENCE=0.75 \
GEMINI_MODEL=gemini-3.1-flash-lite \
GEMINI_VERTEXAI=true \
GEMINI_PROJECT=project-gemini-0306 \
GEMINI_LOCATION=global \
PYTHONPATH=src \
uv run --group dev python -m app.workers.real_gemini_faq_smoke \
  --text "怎么存款？" \
  --seed-default-faq
```

The manual smoke inserts one inbound event, runs `gateway_consumer.process_inbound_event_id`, prints JSON, and by default marks only this smoke event's pending outbound rows as `SKIPPED_MANUAL_SMOKE` instead of sending to LiveChat. Use `--send` only with a real `chat_id` / `thread_id`; send mode dispatches through `sender_worker.process_pending_for_inbound_event`.
