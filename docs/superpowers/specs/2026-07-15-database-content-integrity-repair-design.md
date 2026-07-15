# Database Content Integrity Repair Design

## Goal

Repair the confirmed database content-integrity defects without re-enabling automated work after a LiveChat human takeover. Customer messages must remain auditable and available in conversation history, terminal external-command results must have completion timestamps, and historical repairs must be safe, dry-run-first, and idempotent.

## Scope

This change covers four related areas:

1. Persist accepted customer text and file messages to `conversation_messages` while a thread is `HUMAN_ACTIVE`.
2. Guarantee that an `external_command_results` row inserted as `PROCESSED` has a non-null `processed_at`.
3. Provide a bounded repair command for missing historical customer messages and processed-result timestamps.
4. Begin maintaining the existing `conversation_states` activity pointer fields where the current write paths have unambiguous source records.

The change does not restore deleted parent rows without a backup, invent `chat_user_id`, add broad foreign-key cascades, or change the rule that HUMAN_ACTIVE suppresses all automated customer-visible and external side effects.

## Behavioral Contract

### HUMAN_ACTIVE customer events

For every accepted `MESSAGE_CREATED` or `FILE_RECEIVED` inbound event:

- Persist exactly one customer row in `conversation_messages` using the existing inbound idempotency key.
- Mark the inbound event processed in the same transaction.
- If the thread is `HUMAN_ACTIVE`, do not run or commit LangGraph state, assistant messages, outbound messages, backend commands, Telegram commands, handoff commands, or other external commands.
- If human takeover wins a race after graph execution started, persist the customer message but discard all generated AI and external side effects.

`inbound_events` remains the immutable ingress audit source. `conversation_messages` remains the normalized conversation-history source.

### Processed external results

Every newly inserted `external_command_results` row whose status is `PROCESSED` must have `processed_at` set within the database write. The repository owns this invariant so all callers receive the same behavior. Callers may provide an explicit timestamp; otherwise the repository uses the database clock.

The human-handoff worker continues to emit a terminal audit result because the transfer side effect has already occurred. It does not enqueue that result for the external-result consumer.

### Conversation state pointers

When a customer inbound is committed, set `conversation_states.last_inbound_event_id` to that inbound id. When an outbound is successfully marked SENT, set the matching conversation state's `last_outbound_message_id` in the same transaction where the existing transactional sender path is available. When an external result is applied, persist its normalized result summary in `last_capability_result`.

`chat_user_id` remains null until a separate, explicit LiveChat user identity design exists.

## Architecture

### Gateway service boundary

`GatewayService.process_event()` constructs the normalized customer message for every accepted customer text/file event before deciding whether graph work is allowed. This prevents a missing graph state from being mistaken for a reason to omit conversation history.

### Transaction repository boundary

`GatewayTransactionRepository.process_event_transactionally()` treats the customer message as audit/history data and treats assistant messages, graph state, outbounds, and commands as business side effects.

Its transaction order is:

1. Load or create the thread-scoped conversation.
2. If HUMAN_ACTIVE is already set, insert the customer message idempotently, update `last_inbound_event_id`, mark inbound processed, and commit.
3. Otherwise attempt the graph-state update.
4. If takeover won the graph-state race, insert the customer message idempotently, update `last_inbound_event_id`, mark inbound processed, discard generated side effects, and commit.
5. Otherwise persist the customer message, state, assistant messages, outbounds, commands, pointers, and processed inbound as the normal flow already does.

The customer message is never inserted twice because `uk_conversation_messages_inbound` remains the database idempotency boundary.

### Result repository boundary

`ExternalCommandResultRepository.insert_idempotent_on_connection()` derives the persisted timestamp as follows:

- Non-null caller-provided `processed_at`: preserve it.
- Status `PROCESSED` with no timestamp: use `NOW(6)` in SQL.
- Any non-terminal status with no timestamp: persist null.

This invariant applies independently of worker implementation details.

### Repair command

Add a read-mostly administrative worker with explicit subcommands:

- `customer-messages`: find processed, accepted MESSAGE/FILE inbound rows without a customer conversation message.
- `processed-result-times`: find PROCESSED external results with null `processed_at`.

The default mode is dry-run. Mutation requires `--apply`. Both commands accept `--since`, `--until`, and `--limit`; process rows in primary-key order; print JSON counts; and use idempotent or predicate-guarded writes.

For customer messages, the repair command reconstructs `InboundEvent`, derives `conversation_id` with the existing thread-scoped helper, calls `build_customer_message_from_inbound()`, and inserts through `ConversationMessageRepository`. It must not invoke graph, LLM, sender, backend, Telegram, or LiveChat APIs.

For processed-result timestamps, use `created_at` as the historical completion timestamp because the human-handoff result is inserted only after transfer succeeds. The update predicate remains `status = 'PROCESSED' AND processed_at IS NULL`.

## Historical Orphans

The existing orphan references are stable historical damage from parent-row deletion. This implementation does not automatically delete dependent conversation content or synthesize missing parent records.

Before any orphan cleanup:

1. Export affected row ids and references.
2. Check whether a database backup can restore the deleted parents.
3. If restoration is impossible, handle nulling or archival in a separately approved maintenance operation.

Future retention tooling must understand dependency order. Foreign keys may be considered only after existing orphan rows are resolved and retention requirements are documented.

## Error Handling and Safety

- All normal-path customer message writes remain in the existing inbound processing transaction.
- Repair writes are opt-in, bounded, ordered, and safe to repeat.
- A repair row failure is recorded in command output and does not hide successful prior rows.
- Dry-run output never prints customer text, file URLs, account identifiers, tokens, or payload JSON.
- No production bootstrap or schema mutation is required for this repair.
- Existing dirty-worktree changes outside the scoped files must be preserved.

## Testing

### Unit tests

- HUMAN_ACTIVE text event persists one customer message and no business side effects.
- HUMAN_ACTIVE file event persists attachment history and no business side effects.
- Takeover race preserves the customer message while discarding generated work.
- Normal AI-active processing continues to persist exactly one customer message.
- A PROCESSED result with no timestamp uses database time.
- A caller-provided processed timestamp is preserved.
- A PENDING result keeps a null timestamp.
- Repair dry-run reports candidates without writes.
- Repair apply mode inserts or updates candidates and is idempotent on a second run.

### MySQL integration tests

- Verify the real inbound-message unique index prevents duplicates.
- Verify HUMAN_ACTIVE customer history and inbound processed state commit atomically.
- Verify a PROCESSED result cannot be inserted with null `processed_at` through the repository.
- Verify repair commands against bounded fixtures and repeat execution.

### Deployment verification

After deployment, run read-only checks plus controlled smoke cases:

1. A HUMAN_ACTIVE thread receives one customer text and one file: both appear in `conversation_messages`; no outbound or external command is created.
2. A successful handoff transfer result has non-null `processed_at`.
3. One backend query created after deployment carries `livechat_group_id` and `platform`; its successful result carries those fields plus `merchant_code`.
4. Orphan counters do not increase.

## Rollout

1. Deploy code invariants and regression tests without historical mutation.
2. Run controlled smoke tests and read-only integrity queries.
3. Back up the affected tables.
4. Dry-run both repair subcommands and record counts.
5. Apply timestamp repair.
6. Apply customer-message repair in bounded batches.
7. Re-run integrity queries and application tests.

Rollback of the code deployment does not require schema rollback. Historical rows inserted by the repair remain valid normalized history and are protected against duplication by existing unique indexes.
