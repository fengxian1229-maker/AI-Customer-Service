# P7-A.5 outbound multi-message idempotency shape

Status: schema and repository preparation implemented. No Gateway or sender integration.

## Scope

P7-A.5 prepares `outbound_messages` for future FAQ multi-message rendering while preserving the existing single-text path.

New nullable columns:

- `dedup_key VARCHAR(255) NULL`
- `block_index INT NULL`
- `message_kind VARCHAR(64) NULL`
- `command_type VARCHAR(128) NULL`

New unique key:

- `uk_outbound_messages_dedup_key (dedup_key)`

The existing `uk_inbound_action (inbound_event_id, action_type)` remains in place for backward compatibility with the current Gateway path.

## `action_type` and `command_type`

`action_type` remains the existing sender-facing routing field used by the current text sender path.

`command_type` is the future canonical command identity for generated outbound rows. During the compatibility phase:

- legacy text rows set `command_type = action_type`;
- FAQ plan rows set both `action_type` and `command_type` to the dry-run planner command type, such as `livechat.send_text`, `livechat.send_image`, or `livechat.buttons_preview`;
- no current production sender behavior is changed.

This lets future work migrate sender routing intentionally without breaking existing `action_type` consumers.

## Dedup Key Rules

For future FAQ multi-outbound rows, `dedup_key` comes from the P7-A.4 dry-run plan:

```text
tenant_id:conversation_id:inbound_event_id:faq_block:block_index:message_kind:stable_identity
```

Stable identity rules:

- text: first 16 hex chars of `sha256(text)`
- image: `asset_key`
- buttons: `menu_key`

For legacy single-text rows that do not provide `dedup_key`, `OutboundMessageRepository` derives:

```text
tenant_id:conversation_id:inbound_event_id:action_type
```

This keeps old callers compatible while ensuring new inserts have a stable key.

## Block Index

`block_index` is nullable for legacy rows.

For FAQ plan rows:

- starts at `0`;
- follows preview block order;
- remains stable for the same canonical `answer_blocks`.

The database does not currently enforce sequential block indexes. That remains application-level behavior until production multi-outbound rendering is connected.

## Status and Retry Semantics

Existing semantics remain:

- `PENDING`: row is ready for a sender worker to process;
- `SENT`: row was sent successfully;
- `RETRYABLE`: transient failure, retry count can increase;
- `FAILED_CONFIG`, `FAILED_BUSINESS`, `FAILED_UNKNOWN`: existing failure classes remain available;
- `retry_count`: counts send attempts or retry scheduling according to current sender behavior.

P7-A.5 does not change sender retry behavior.

## Partial Failure Recovery

Future multi-message FAQ sending should recover by row-level idempotency:

1. Gateway creates one row per FAQ block with stable `dedup_key`.
2. If writing the batch is retried, already-written rows are treated as duplicates.
3. Sender processes rows independently by `status`.
4. If block `0` is sent and block `1` fails, block `0` remains `SENT` and block `1` can remain `RETRYABLE` or failure-classed.
5. Recovery retries only unsent or retryable rows; it does not need to recreate sent rows.

This requires future production integration to define ordering guarantees before enabling real multi-message sends.

## Current Boundaries

This stage intentionally does not:

- modify Gateway FAQ output logic;
- make `rag_node` insert multiple `outbound_messages`;
- modify `command_planner_node`;
- modify `sender_worker`;
- implement LiveChat upload or image sending;
- send real images;
- add a non-null or DB-enforced block index sequence;
- change LangGraph topology;
- introduce LLM final answer generation.

The only new conversion helper is `faq_plan_to_outbound_rows(...)`, which converts a P7-A.4 dry-run plan into row dictionaries. It does not insert them.

## Tests

Coverage includes:

- schema fields and unique dedup key;
- bootstrap idempotency for new columns/indexes;
- repository insertion of legacy and FAQ multi-block metadata;
- FAQ plan to outbound row conversion;
- existing unit suite and MySQL integration suite.

## Next Step

P7-A.6 should still avoid real image sending. The next safe increment is a Gateway-offline adapter test that takes a known RAG context and verifies the future row batch shape without persisting it, or a design for sender ordering and unsupported `message_kind` handling before enabling any production writes.
