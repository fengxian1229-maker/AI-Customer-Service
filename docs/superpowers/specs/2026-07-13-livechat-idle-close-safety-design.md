# LiveChat Idle Close Safety

## Goal

Prevent the idle timer from sending a follow-up or close message, or deactivating a LiveChat chat, when customer activity has already reached the system. If the idle timer cannot generate its text because both final-reply models fail, transfer the conversation to a human instead of continuing the idle close path.

## Safety Boundary

The safety decision is chat-scoped because LiveChat deactivation operates on `chat_id`, not on an individual thread. Customer activity in any thread belonging to the same chat blocks idle actions for an older thread.

Customer activity is detected from valid external/customer inbound events using `COALESCE(occurred_at, created_at)`. The check includes both processed and unprocessed events so a newer thread cannot be hidden from an older conversation's idle timer.

The system cannot detect text that a customer is only typing locally and has not submitted. The guarantee begins when the customer event is persisted in `inbound_events`.

## Idle Flow

Before a follow-up or close action, the worker checks for customer activity after the assistant message that started the idle cycle. It checks again after final-reply generation and immediately before sending text. Before deactivating the chat, it performs one final check.

If any check finds customer activity, the worker sends no further idle text and does not deactivate the chat. If an activity check fails, the worker fails closed: it records/returns a skipped or failed safety result and does not deactivate the chat.

The checks narrow the race window to the external LiveChat deactivation call. They do not claim atomicity with a customer event that has not yet reached this service.

## LLM Failure

The existing primary and failover final-reply models remain in use. For idle follow-up or close generation, a final-reply result with `timeout`, `exception`, or `provider_failure` is terminal for the idle path.

The worker then:

1. Sends a deterministic, localized AI-failure handoff notice without another LLM call.
2. Changes the conversation to `HANDOFF_REQUESTED` with `active_workflow=human_handoff`.
3. Persists AI-failure metadata in slot memory.
4. Enqueues the existing `human_handoff.requested` external command idempotently.
5. Does not send the follow-up/close text and does not deactivate the chat.

If the handoff notice cannot be sent, the conversation remains assigned to the human-handoff path and the idle timer remains unable to close it.

## Public Test Seams

Tests exercise `process_idle_conversation()` as the application boundary, with the database, LiveChat API, and LLM treated as external boundaries. Observable assertions cover sent text, chat deactivation, result status, persisted conversation state, and the requested human handoff.

Repository behavior is tested through the public customer-activity query. It must detect activity across threads and fall back to inbound row creation time when `occurred_at` is absent.

## TDD Slices

1. Customer activity arriving during final-reply generation prevents idle text and chat deactivation.
2. Idle final-reply failure requests human handoff and never closes the chat.
3. Customer activity in a different thread of the same chat blocks idle actions, including events without `occurred_at`.
4. Existing silent-customer follow-up and close behavior remains unchanged.

Each slice follows red then minimal green. Refactoring, if needed, occurs only after all behavior is green.
