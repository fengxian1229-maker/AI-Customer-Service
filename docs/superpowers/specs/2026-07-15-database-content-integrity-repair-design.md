# Database Content Integrity Repair Design

## Goal

Apply two small fixes to stop confirmed content-integrity defects:

1. Keep customer text and file history when a LiveChat thread is `HUMAN_ACTIVE`.
2. Ensure every newly inserted `PROCESSED` external-command result has `processed_at`.

No schema change is required.

## Scope

### HUMAN_ACTIVE customer history

For every accepted `MESSAGE_CREATED` or `FILE_RECEIVED` event, build and persist one customer `conversation_messages` row.

When the thread is `HUMAN_ACTIVE`:

- Insert the customer message idempotently.
- Mark the inbound event processed in the same transaction.
- Do not persist graph state, assistant messages, outbound messages, or external commands.

Apply the same rule when human takeover wins a race after graph execution has started: keep the customer message and discard generated AI work.

The existing `uk_conversation_messages_inbound` unique index prevents duplicate customer history.

### Processed result timestamp

In `ExternalCommandResultRepository.insert_idempotent_on_connection()`, derive the stored timestamp as follows:

- Preserve a caller-provided `processed_at`.
- When `status == "PROCESSED"` and no timestamp is provided, use the database time `NOW(6)`.
- For non-processed rows without a timestamp, keep `processed_at` null.

The repository owns this invariant so the human-handoff worker and any future caller behave consistently.

## Files

- Modify `src/app/services/gateway.py` so accepted customer MESSAGE/FILE events build customer history independently of graph execution.
- Modify `src/app/db/repositories.py` so HUMAN_ACTIVE early-return and takeover-race paths insert customer history before marking inbound processed.
- Modify `src/app/db/repositories.py` so newly inserted PROCESSED results receive a timestamp.
- Update focused unit tests in `tests/unit/test_gateway.py` and `tests/unit/test_repositories.py`.

## Explicitly Out of Scope

- No general repair CLI.
- No automatic cleanup of historical orphan references.
- No foreign keys or schema migration.
- No changes to `chat_user_id`, `last_inbound_event_id`, `last_outbound_message_id`, or `last_capability_result`.
- No automatic production-data backfill in the application deployment.
- No changes to merchant routing.

Historical backfill can be performed later with separately reviewed, bounded SQL after the code fixes are deployed.

## Tests

- HUMAN_ACTIVE text event persists one customer message and no business side effects.
- HUMAN_ACTIVE file event preserves attachment history and no business side effects.
- Takeover race preserves the customer message and discards generated work.
- Normal AI-active processing continues to persist exactly one customer message.
- A PROCESSED result without a caller timestamp uses `NOW(6)`.
- A caller-provided timestamp is preserved.
- A PENDING result keeps a null timestamp.
- Run the focused modules, then the complete unit suite.

## Deployment Check

After deployment, use read-only queries to confirm:

- A controlled HUMAN_ACTIVE customer event has a matching customer `conversation_messages` row.
- No outbound or external command is created for that event.
- A new successful handoff result has non-null `processed_at`.
- Existing orphan counters do not increase.
