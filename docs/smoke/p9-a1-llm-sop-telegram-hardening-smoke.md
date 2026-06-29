# P9-A.1 LLM SOP Telegram Hardening Smoke

## Goal

Verify the outbound-only SOP loop:

```text
deposit_missing complete data
  -> gateway_consumer
  -> external_commands telegram.send_case_card
  -> external_command_worker dry-run or --execute-telegram
  -> external_result_consumer
  -> conversation_state waiting_backend with telegram_message_id
  -> customer supplement
  -> telegram.append_to_case with reply_to original Telegram card
```

This smoke does not cover Telegram inbound, Telegram webhook, `getUpdates`, or staff replies flowing back into LiveChat.

## Environment

Do not store real tokens in docs or commits.

```bash
LLM_PROVIDER=gemini
LLM_ROUTER_MODE=guarded_authoritative
LLM_SOP_SLOT_ENABLED=true
LLM_SOP_SLOT_MIN_CONFIDENCE=0.70
TELEGRAM_SOP_ENABLED=true
TELEGRAM_BOT_TOKEN=***
TELEGRAM_TEST_GROUP=-100xxxx
TELEGRAM_UPLOAD_ATTACHMENTS_VIA_DOWNLOAD=true
TELEGRAM_ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS=15
TELEGRAM_ATTACHMENT_MAX_BYTES=10485760
```

## Dry-Run Smoke

1. Insert or simulate a `deposit_missing` text event, for example `mi deposito no llego`.
2. Insert or simulate a username event, for example `mi usuario es andy123`.
3. Insert or simulate a screenshot/file event with an attachment URL.
4. Run:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.gateway_consumer --once --limit 20
```

5. Verify `external_commands` has `telegram.send_case_card`.
6. Run:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.external_command_worker --once --dry-run --emit-result
PYTHONPATH=src uv run --group dev python -m app.workers.external_result_consumer --once
```

7. Verify `conversation_states`:

- `active_workflow=deposit_missing`
- `workflow_stage=waiting_backend`
- `slot_memory.telegram_message_id` exists

8. Insert a supplement event, such as `交易号 TX123456` or a new attachment.
9. Run gateway again and verify `external_commands` has `telegram.append_to_case` with `payload.telegram_message_id`.

## Real Telegram Test Group Smoke

Use `TELEGRAM_TEST_GROUP`; do not use the formal finance group for P9-A.1.

```bash
TELEGRAM_SOP_ENABLED=true TELEGRAM_BOT_TOKEN=*** TELEGRAM_TEST_GROUP=-100xxxx PYTHONPATH=src \
uv run --group dev python -m app.workers.external_command_worker --once --execute-telegram --emit-result
PYTHONPATH=src uv run --group dev python -m app.workers.external_result_consumer --once
```

Success criteria:

- Telegram main card exists.
- Main screenshot is sent as a reply to the main card.
- Later supplements are replies to the original main card.
- LiveChat customer receives the "transferred to backend" and "supplemented to backend" messages.
- DB state remains `waiting_backend`.

## Troubleshooting

- No `telegram_message_id`: check whether `external_result_consumer` consumed `telegram.case.created`.
- Attachment fallback: check LiveChat attachment authorization and whether Telegram can parse the file.
- `RETRYABLE`: Telegram 429/5xx/timeouts/network failures should not be converted to fallback text.
- Orphan append: `telegram.append_to_case` without `telegram_case_id` and `telegram_message_id` is expected to fail.
