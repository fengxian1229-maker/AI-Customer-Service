# P12 External Result Flow TODO

## Scope

This document records the remaining result-return path for external capabilities after the Graph LLM flow migration.

External capabilities are not normal customer-message routes. They are commands emitted by business nodes, persisted as `external_commands`, and executed by workers.

## Current command trigger path

Customer message path:

```text
Gateway
  -> LangGraph
  -> rewrite_question_node
  -> language_policy_node
  -> intent_router_node
  -> sop_node
  -> final_reply_node
  -> command_planner_node
  -> Gateway command split
```

Command split:

```text
livechat.send_text -> outbound_messages
telegram.send_case_card -> external_commands
telegram.append_to_case -> external_commands
backend.query -> external_commands
pending_reply.lookup -> external_commands
human_handoff.requested -> external_commands
```

## Remaining result-return path

Implement and test these paths in a later task:

```text
external_command_results
  -> external_result_consumer
  -> backend_result_node / staff_reply_result_node
  -> final_reply_node
  -> command_planner_node
  -> outbound_messages
```

## Requirements

1. Telegram staff replies must be correlated by `telegram_case_id`, `telegram_message_id`, or `conversation_id`.
2. Backend query results must be correlated by `external_command_id` or `conversation_id`.
3. Result-driven events must not re-enter the normal customer-message rewrite/router path.
4. Result-driven flows must preserve `slot_memory`, `active_workflow`, and `workflow_stage`.
5. Final customer wording must still pass through `final_reply_node`.
6. Backend/TG facts must remain verified facts and must not be invented by the final reply model.

## Suggested tests

- Backend result resumes the original conversation and produces one customer reply.
- Telegram staff reply resumes the original conversation and produces one customer reply.
- Result-driven flow does not call `rewrite_question_node` or `intent_router_node`.
- Result-driven flow calls `final_reply_node` before `command_planner_node`.
- Missing correlation metadata puts the result into retry/error handling instead of creating a new conversation.
