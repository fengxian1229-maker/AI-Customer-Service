# P9-A Telegram SOP Closed Loop Smoke

P9-A adds the minimal deterministic side-effect loop for `deposit_missing` and `withdrawal_missing`:

```text
inbound_events
  -> gateway_consumer
  -> SOP slot extractor / policy / reply planner
  -> external_commands telegram.send_case_card
  -> external_command_worker --execute-telegram
  -> external_command_results telegram.case.created
  -> external_result_consumer
  -> conversation_states stays waiting_backend
```

Required slots before a main Telegram case is allowed:

- `deposit_missing`: `account_or_phone`, `deposit_screenshot`
- `withdrawal_missing`: `account_or_phone`, `withdrawal_screenshot`

The LLM/router may identify SOP intent, but the system still performs deterministic policy checks before creating a Telegram command. The worker is the only place that sends Telegram messages.

Telegram env:

```bash
TELEGRAM_SOP_ENABLED=true
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_TEST_GROUP=<test_group_chat_id>
# Optional explicit target:
TELEGRAM_SOP_TARGET_CHAT_ID=<chat_id>
TELEGRAM_SOP_MESSAGE_THREAD_ID=<topic_id>
TELEGRAM_FORCE_NO_TOPIC=false
TELEGRAM_REQUEST_TIMEOUT_SECONDS=15
```

Dry-run command worker:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.external_command_worker --once --dry-run --emit-result
```

Real Telegram execution:

```bash
TELEGRAM_SOP_ENABLED=true TELEGRAM_BOT_TOKEN=<bot_token> TELEGRAM_TEST_GROUP=<test_group_chat_id> PYTHONPATH=src \
uv run --group dev python -m app.workers.external_command_worker --once --execute-telegram --emit-result
```

Consume Telegram results:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.external_result_consumer --once
```

P9-A sender behavior:

- Sends the main case card with `sendMessage`.
- Sends screenshot URLs with `sendPhoto(photo=<url>)` replying to the main card.
- Falls back to a text message containing the URL if `sendPhoto` fails.
- Appends waiting-backend supplements with `telegram.append_to_case`, replying to the stored main Telegram message id.

Known P9-A.1 follow-up:

- LiveChat authenticated attachment download plus Telegram multipart upload is not implemented yet.
- Telegram inbound, webhook/getUpdates, and staff replies flowing back into LiveChat are intentionally out of scope.
