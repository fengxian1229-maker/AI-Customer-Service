# P8-C Gemini Human Handoff Smoke

## Test Goal

- Verify the Gemini guarded_authoritative LLM router can trigger `human_handoff` from LLM intent recognition.
- Verify route-first router output normalization is effective.
- Verify `human_handoff.requested` external command generation.
- Verify LiveChat transfer to group `23` succeeds.
- Verify the conversation enters `HUMAN_ACTIVE`.
- Verify Gateway bot suppression after handoff is effective.

## Test Environment

- `LLM_PROVIDER=gemini`
- `LLM_ROUTER_MODE=guarded_authoritative`
- `LLM_ROUTER_FALLBACK_TO_DETERMINISTIC=false`
- `LIVECHAT_HANDOFF_ENABLED=true`
- `LIVECHAT_HANDOFF_TARGET_GROUP_ID=23`
- Gemini model default: `gemini-3.1-flash-lite`
- Vertex AI default: `gemini_vertexai=true`
- Vertex AI project default: `gemini_project=project-gemini-0306`
- Vertex AI location default: `gemini_location=global`

## Test Input

```text
The automatic answers are not resolving this after several attempts. Please have this conversation reviewed by a specialist who can take over from here.
```

## Key Database Records

- `inbound_event_id=27`
- `chat_id=TH1D9I915U`
- `thread_id=TH1D9I916U`
- `graph_checkpoint_runs.status=SUCCEEDED`
- `graph_checkpoint_runs.metadata_json.llm_router.status=accepted`
- `graph_checkpoint_runs.metadata_json.llm_router.final_route=human_handoff`
- `graph_checkpoint_runs.metadata_json.llm_router.route_source=llm_guarded_authoritative`
- `graph_checkpoint_runs.metadata_json.llm_router.final_intent=explicit_human_request`
- `graph_checkpoint_runs.metadata_json.llm_router.confidence=1.0`
- `graph_checkpoint_runs.metadata_json.llm_router.fallback_reason=NULL`
- `graph_checkpoint_runs.metadata_json.llm_router.hard_guard=NULL`
- `graph_checkpoint_runs.metadata_json.llm_router.error_message=NULL`
- `external_command_id=2`
- `external_commands.command_type=human_handoff.requested`
- `external_commands.status=PENDING` before execute smoke
- Final conversation status: `HUMAN_ACTIVE`
- Final active workflow: `human_handoff`
- Final workflow stage: `transferred`
- Post-handoff `inbound_event_id=31`

## Worker Output Summary

### polling_receiver

- Received and persisted the specialist-review handoff request.
- Created `inbound_event_id=27`.
- Preserved `chat_id=TH1D9I915U` and `thread_id=TH1D9I916U`.

### gateway_consumer / process_inbound_event_id

- `processed=1`
- `failed=0`
- `router_status=accepted`
- `final_route=human_handoff`
- `route_source=llm_guarded_authoritative`
- `final_intent=explicit_human_request`
- Generated one `human_handoff.requested` external command.
- Did not enqueue normal bot outbound messages for the handoff route.

### human_handoff_smoke plan-only

- `smoke_success=true`
- `dry_run=true`
- `plan_only=true`
- `execute_human_handoff=false`
- `command_id=2`
- `would_send_notice=true`
- `would_transfer=true`
- `conversation_status_before=HANDOFF_REQUESTED`
- `conversation_status_after=HANDOFF_REQUESTED`
- `external_command_status=PENDING`

### human_handoff_smoke execute

- `smoke_success=true`
- `dry_run=false`
- `plan_only=false`
- `execute_human_handoff=true`
- `command_id=2`
- `lease_attempted=true`
- `lease_acquired=true`
- `transfer_attempted=true`
- `transfer_success=true`
- `transfer_blocked=false`
- `external_command_status=SENT`
- `conversation_status_before=HANDOFF_REQUESTED`
- `conversation_status_after=HUMAN_ACTIVE`
- `active_workflow=human_handoff`
- `workflow_stage=transferred`

Transfer result:

- `status=TRANSFERRED`
- `chat_id=TH1D9I915U`
- `target_group_id=23`
- `ignore_agents_availability=true`
- `ignore_requester_presence=true`

### Post-Handoff Bot Suppression

- `SMOKE_INBOUND_EVENT_ID=31`
- `chat_id=TH1D9I915U`
- `conversation.status=HUMAN_ACTIVE`
- `active_workflow=human_handoff`
- `workflow_stage=transferred`
- `should_reply=false`
- `graph_state=null`
- `outbound_messages=[]`
- `external_commands=[]`
- `processed=1`
- `failed=0`
- `enqueued=0`

## Result Semantics

`external_command_results.status=PENDING` means the result is waiting for `external_result_consumer` to consume it and apply downstream effects.

A real `human_handoff.transfer_chat.result` should not remain `PENDING`, because the real transfer path has already completed LiveChat transfer, marked `external_commands` as `SENT`, and updated `conversation_states` to `HUMAN_ACTIVE`.

The P8-C hardening records real handoff transfer results as `PROCESSED`. This keeps the row as an audit record and prevents `external_result_consumer` from treating it as work that should generate a second bot reply.

Dry-run and mock result rows keep their existing `PENDING -> external_result_consumer` semantics.

## Conclusion

- LLM accepted human_handoff route: PASSED
- Router output normalization: PASSED
- External command generation: PASSED
- Plan-only: PASSED
- LiveChat transfer: PASSED
- Conversation state update: PASSED
- Post-handoff Gateway bot suppression: PASSED

Overall P8-C smoke result: PASSED.
