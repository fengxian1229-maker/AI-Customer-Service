# Durable Checkpoint Storage Design

Date: 2026-06-26

## 1. Current Checkpoint Status

Current LangGraph checkpoint modes:

- `off`
  Production default. No checkpointer is created.
- `memory`
  Local/dev/test only. Uses `InMemorySaver()` and is not durable across process restarts.
- `mysql`
  Recognized as a planned durable mode, but not enabled in P5-A.

P5-A does not implement a real MySQL LangGraph saver. In this phase, `mysql` is configuration-recognizable but must fail fast with a clear error instead of silently falling back.

## 2. Why Durable Checkpoints Are Needed

Durable checkpoints are needed for:

- LangGraph execution state persistence.
- Post-restart debugging and state inspection.
- A foundation for future interrupt/resume.
- A foundation for longer-running SOP paths, including waiting-for-backend and human-confirmation style workflows.

The goal is not to replace current business tables. The goal is to preserve graph runtime context in a durable layer that can later support operator tooling and controlled recovery flows.

## 3. What P5-A Does Not Do

P5-A explicitly does not implement:

- interrupt/resume
- human approval interruption flows
- WebSocket receiver work
- Webhook receiver work
- changes to the current RAG main path
- automatic business-flow recovery from checkpoints
- a production-ready MySQL LangGraph saver

This keeps checkpoint work isolated from ingress, RAG, Telegram, backend, and workflow-product changes.

## 4. Boundary With Existing Tables

Checkpoint persistence must not replace existing business and audit tables.

- `conversation_states`
  Business-state projection such as `active_workflow`, `workflow_stage`, `slot_memory`, and current status.
- `conversation_messages`
  Durable customer/assistant/external summary history.
- `graph_run_errors`
  Graph execution failure audit, including sanitized state snapshots.
- checkpoint tables
  LangGraph runtime persistence and checkpoint metadata only.

In other words:

- business state remains in `conversation_states`
- message history remains in `conversation_messages`
- failure audit remains in `graph_run_errors`
- checkpoint storage is an additional runtime layer, not a substitute

## 5. Thread ID Design

The project must continue using:

- `conversation_id` as LangGraph `configurable.thread_id`

The project must not use:

- LiveChat `thread_id` as the LangGraph checkpoint thread id

Reason:

- `conversation_id` is already the stable cross-message execution identity used by graph config and graph debug helpers.
- LiveChat `thread_id` is channel-specific and not the correct durable runtime boundary for the workflow graph.

## 6. Security And Size Controls

Checkpoint-related state and metadata must not store secrets.

Do not persist:

- token
- access_token
- api_key
- password
- secret

Large or noisy fields must be size-controlled before persistence or audit usage:

- `rag_context`
- attachments
- raw payload blobs
- oversized rewritten input / snapshots

P5-A keeps this conservative:

- `graph_run_errors` already stores sanitized snapshots
- new checkpoint metadata only stores small metadata summaries
- full graph state is not written into `graph_checkpoint_runs.metadata_json`

Recommended small metadata examples:

- `checkpoint_mode`
- config summary
- node count
- bounded flags or counters

## 7. P5-A Schema Scope

P5-A adds a project-owned metadata table:

- `graph_checkpoint_runs`

This table is for provider status and investigation metadata. It is not presented as LangGraph’s internal saver schema.

Suggested responsibilities:

- record that a graph run had checkpoint mode `off|memory|mysql`
- record whether checkpoint-related runtime completed or failed
- record a later `latest_checkpoint_id` when a durable saver exists
- record lightweight runtime metadata from `gateway_consumer -> GatewayService -> GraphCheckpointRunRepository`

This intentionally avoids guessing LangGraph internal MySQL saver schema before P5-B.

`graph_checkpoint_runs` remains metadata/audit only:

- it does not replace `graph_run_errors`
- it does not store full LangGraph state
- it does not provide admin CLI or resume behavior in P5-A/P5-A.1

## 8. Phase Breakdown

- P5-A
  Design, schema preparation, provider boundary, metadata repository, tests.
- P5-B
  Real MySQL checkpointer implementation and controlled runtime integration.
- P5-C
  Checkpoint debug/admin tooling.
- P5-D
  interrupt/resume.

## 9. Runtime Recommendation

Current runtime recommendation:

- `LANGGRAPH_CHECKPOINT_MODE=off`
  default and safest production behavior
- `LANGGRAPH_CHECKPOINT_MODE=memory`
  local/dev/test only
- `LANGGRAPH_CHECKPOINT_MODE=mysql`
  do not use in smoke or production during P5-A; it is recognized but intentionally not enabled
