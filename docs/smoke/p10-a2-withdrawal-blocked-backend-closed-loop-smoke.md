# P10-A.2 Withdrawal Blocked Backend Closed-Loop Smoke

## Goal

Run and diagnose the real closed loop for `withdrawal_blocked_or_rollover`:

```text
LiveChat message
  -> polling_receiver
  -> gateway_consumer
  -> withdrawal_blocked_or_rollover SOP
  -> immediate LiveChat outbound
  -> external_commands.backend.query
  -> external_command_worker --execute-backend --emit-result
  -> external_command_results.backend.query.result
  -> external_result_consumer
  -> backend answer outbound
  -> sender_worker
  -> conversation_state completed
```

This round intentionally does not add DB-backed tenant backend config or tenant runtime profile. Backend config still comes from the explicit env fallback added in P10-A.1.

## Env

Keep real values in `.env` or shell only:

```bash
LIVECHAT_AGENT_ACCESS_TOKEN=...
LIVECHAT_ACCOUNT_ID=...
LIVECHAT_ALLOWED_GROUP_IDS=23

BACKEND_QUERY_ENABLED=true
BACKEND_PROVIDER_TYPE=tac
BACKEND_BASE_URL=...
BACKEND_AUTHORIZATION=...
BACKEND_MERCHANT_CODE=...
BACKEND_LOGIN_OPERATOR=...
BACKEND_LOGIN_PASSWORD=...
BACKEND_LOGIN_MERCHANT=...
```

## Step 1: Probe TAC

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.tac_backend_probe turnover <username_or_phone> <merchantCode>
```

Expected: sanitized JSON with `player_found`, `active_requirements_count`, `remaining_turnover`, and `is_met`. If probe fails, stop before LiveChat smoke and record the safe error.

## Step 2: Send LiveChat Test Message

Use an allowed test group. Example:

```text
提款不了，提示还有流水，用户名是 <username>
```

Do not paste real customer-sensitive values into docs.

## Step 3: Poll Inbound

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.polling_receiver --once --groups 23
```

Record `inbound_event_id`, `chat_id`, and `thread_id`.

SQL:

```sql
SELECT id, chat_id, thread_id, standard_event_type, processed, ignored, created_at
FROM inbound_events
ORDER BY id DESC
LIMIT 10;
```

## Step 4: Gateway

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.gateway_consumer --once
```

Expected immediate outbound:

```text
一般无法提款通常与流水要求或风控限制有关。已收到你的资料，我们正在进一步查询。
```

SQL:

```sql
SELECT id, conversation_id, inbound_event_id, chat_id, status, payload_json, created_at
FROM outbound_messages
WHERE inbound_event_id = <inbound_event_id>
ORDER BY id ASC;

SELECT id, conversation_id, inbound_event_id, command_type, status, payload_json, created_at
FROM external_commands
WHERE inbound_event_id = <inbound_event_id>
ORDER BY id ASC;
```

Expected command: `command_type='backend.query'`, `status='PENDING'`, `payload_json.intent='withdrawal_blocked_or_rollover'`.

## Step 5: Send Immediate Reply

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.sender_worker --once
```

Expected: the immediate outbound becomes `SENT`.

## Step 6: Execute Backend Query

```bash
BACKEND_QUERY_ENABLED=true \
BACKEND_PROVIDER_TYPE=tac \
BACKEND_BASE_URL=... \
BACKEND_AUTHORIZATION=... \
BACKEND_MERCHANT_CODE=... \
BACKEND_LOGIN_OPERATOR=... \
BACKEND_LOGIN_PASSWORD=... \
BACKEND_LOGIN_MERCHANT=... \
PYTHONPATH=src uv run --group dev python -m app.workers.external_command_worker --once --execute-backend --emit-result
```

SQL:

```sql
SELECT id, command_type, status, retry_count, last_error, payload_json
FROM external_commands
WHERE inbound_event_id = <inbound_event_id>
ORDER BY id ASC;

SELECT id, external_command_id, result_type, status, result_json
FROM external_command_results
WHERE inbound_event_id = <inbound_event_id>
ORDER BY id ASC;
```

Expected success: backend command `SENT`; `backend.query.result` exists with `result_json.status='success'` and non-empty `answer`.

If `result_json.status='failed'`, the smoke is not a successful backend query. Continue only to verify safe fallback handling.

## Step 7: Consume Backend Result

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.external_result_consumer --once
```

SQL:

```sql
SELECT id, result_type, status, last_error
FROM external_command_results
WHERE inbound_event_id = <inbound_event_id>
ORDER BY id ASC;

SELECT id, conversation_id, inbound_event_id, status, payload_json
FROM outbound_messages
WHERE inbound_event_id = <inbound_event_id>
ORDER BY id ASC;

SELECT id, conversation_id, chat_id, status, active_workflow, workflow_stage, slot_memory, updated_at
FROM conversation_states
WHERE conversation_id = 'livechat:<chat_id>';
```

Expected success: result `PROCESSED`, backend answer outbound `PENDING`, conversation `AI_ACTIVE` / `completed`.

For failed backend results, the consumer marks the result `PROCESSED` and writes the safe fallback:

```text
后台查询暂时无法完成，我们会继续为你人工复核，请稍候。
```

## Step 8: Send Backend Answer

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.sender_worker --once
```

Expected: backend answer outbound becomes `SENT`.

## Step 9: Diagnose Or Assert

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.backend_sop_smoke_admin latest --chat-id <chat_id>
PYTHONPATH=src uv run --group dev python -m app.workers.backend_sop_smoke_admin by-inbound --inbound-event-id <inbound_event_id>
PYTHONPATH=src uv run --group dev python -m app.workers.backend_sop_smoke_admin assert-closed-loop --inbound-event-id <inbound_event_id>
```

Success status:

```json
{
  "smoke_status": "BACKEND_ANSWER_SENT",
  "closed_loop": true
}
```

## Success Criteria

- `inbound_events.processed=1`
- No `graph_run_errors` for the inbound event.
- `external_commands.backend.query.status='SENT'`
- `external_command_results.backend.query.result.status='PROCESSED'`
- `result_json.status='success'`
- `result_json.answer` is non-empty.
- Backend answer outbound is `SENT`.
- `conversation_states.status='AI_ACTIVE'`
- `conversation_states.workflow_stage='completed'`
- `conversation_messages` contains backend summary or assistant answer.

## Failure Table

| Symptom | Likely Cause | Check |
| --- | --- | --- |
| `NO_INBOUND` | Polling did not ingest the test chat | `inbound_events` latest rows |
| `GATEWAY_NOT_PROCESSED` | Gateway not run or failed | `gateway_consumer --once`, `graph_run_errors` |
| `SOP_NOT_TRIGGERED` | Message did not route to withdrawal blocked SOP or identity missing | inbound text, `external_commands` |
| `BACKEND_COMMAND_PENDING` | Backend worker not run | `external_command_worker --execute-backend --emit-result` |
| `BACKEND_COMMAND_SENT` | Result was not emitted | use `--emit-result` and check `external_command_results` |
| `BACKEND_RESULT_PENDING` | Result consumer not run | `external_result_consumer --once` |
| `BACKEND_ANSWER_OUTBOUND_PENDING` | Sender not run or LiveChat send failed | `sender_worker --once`, `outbound_messages.last_error` |
| `FAILED` | Graph/backend/result/sender failure | admin JSON and SQL error fields |

## Safety Boundary

- TAC provider is read-only.
- Real backend access is default-off through `BACKEND_QUERY_ENABLED=false`.
- Secrets must stay out of Git and diagnostic JSON.
- LLM/Gemini does not decide backend calls and does not generate backend conclusions.
- No third-party backend write operations are implemented.
- DB-backed tenant backend config remains out of scope for this round.
