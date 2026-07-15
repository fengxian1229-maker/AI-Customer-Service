# LiveChat Handoff Closed-Loop Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LiveChat human handoff deterministic, idempotent, failure-safe, and automatically triggered after two consecutive disputes of the same backend conclusion.

**Architecture:** Keep the existing Gateway, transactional outbox, external command, sender, and LiveChat worker boundaries. Persist a compact backend-conclusion record in `slot_memory`, route repeated disputes before the LLM router, keep failed handoffs in `HANDOFF_REQUESTED` with details in `handoff_state`, and require a sent ack before one real `transfer_chat` call.

**Tech Stack:** Python 3.13, LangGraph workflow nodes, aiomysql/MySQL, pytest, existing repository and worker abstractions.

## Global Constraints

- Do not add a Handoff Coordinator service or a `HANDOFF_FAILED` conversation status.
- Do not write to the production database from tests.
- Use `ai_customer_service_test` for MySQL integration tests and abort if the configured database is not the test database.
- Only a successful LiveChat `transfer_chat` may set `conversation_states.status = HUMAN_ACTIVE`.
- `HANDOFF_REQUESTED` permits only the matching handoff ack; all ordinary bot work remains silent.
- Preserve unrelated pre-existing workspace changes. Before staging, inspect the exact diff and do not commit unrelated hunks from dirty files.
- Every behavior change follows RED, GREEN, REFACTOR; a test that passes before implementation does not prove the missing behavior and must be revised.

---

### Task 1: Complete the Ack-to-Transfer State Boundary

**Files:**
- Modify: `src/app/workers/external_result_consumer.py:95-230`
- Modify: `src/app/workers/sender_worker.py:90-310`
- Modify: `src/app/workers/external_command_worker.py:300-420, 790-850`
- Modify: `src/app/db/repositories.py:500-560, 913-929`
- Test: `tests/unit/test_external_result_consumer.py`
- Test: `tests/unit/test_sender_worker.py`
- Test: `tests/unit/test_external_command_worker.py`
- Test: `tests/unit/test_repositories.py`

**Interfaces:**
- Consumes: `graph_state.status`, `conversation.active_workflow`, `outbound_messages.payload_json.handoff_ack`, and `ExternalCommandRepository` lease/status operations.
- Produces: a sent handoff ack followed by at most one transfer attempt; `ConversationRepository.record_handoff_failure(conversation_id: str, failure: dict) -> bool` stores failure audit without leaving `HANDOFF_REQUESTED`.

- [ ] **Step 1: Verify the existing backend-result ack baseline**

The dirty worktree already contains an ack marker change and an assertion for it. Extend the existing `test_backend_player_not_found_second_distinct_value_requests_handoff` with correlation assertions:

```python
outbound = outbound_repository.inserted[0]
command = transaction_repository.external_commands[0]
assert outbound["payload_json"]["handoff_ack"] is True
assert outbound["inbound_event_id"] == command["inbound_event_id"]
assert conversation_repository.updated[0][1]["status"] == "HANDOFF_REQUESTED"
```

- [ ] **Step 2: Run the focused result-consumer baseline**

Run:

```bash
uv run --group dev pytest tests/unit/test_external_result_consumer.py -q -k correlated_handoff_ack
```

Expected: PASS if the pre-existing partial ack change is intact. If it fails, stop and diagnose the overlap before editing production code.

- [ ] **Step 3: Verify the existing correlated ack implementation**

Use one `graph_state` value and pass the status into the outbox builder:

```python
graph_state = handler["graph_state"]
outbound = build_result_outbox(
    row,
    handler["text"],
    handoff_ack=graph_state.get("status") == "HANDOFF_REQUESTED",
)
```

`build_result_outbox` must add only the marker; `ExternalResultTransactionRepository.process_result_transactionally` remains responsible for committing the state, outbox, and external command in one transaction:

```python
if handoff_ack:
    outbound["payload_json"]["handoff_ack"] = True
```

- [ ] **Step 4: Verify the existing sender-gate test matrix**

Cover the three states explicitly:

```python
@pytest.mark.parametrize(
    ("status", "workflow", "is_ack", "expected"),
    [
        ("HANDOFF_REQUESTED", "human_handoff", True, "SENT"),
        ("HANDOFF_REQUESTED", "human_handoff", False, "SKIPPED_HUMAN_ACTIVE"),
        ("HUMAN_ACTIVE", "human_handoff", True, "SKIPPED_HUMAN_ACTIVE"),
    ],
)
def test_sender_handoff_gate(status, workflow, is_ack, expected):
    message = make_message() | {
        "conversation_status": status,
        "conversation_active_workflow": workflow,
        "payload_json": {"type": "message", "text": "handoff", "handoff_ack": is_ack},
    }
    result = asyncio.run(process_pending_message(repository, client, message))
    assert result["status"] == expected
```

- [ ] **Step 5: Run the sender gate baseline**

Run:

```bash
uv run --group dev pytest tests/unit/test_sender_worker.py -q -k sender_handoff_gate
```

Expected: PASS if the pre-existing partial sender change is intact. A failure is an overlap/regression to diagnose before adding new behavior.

- [ ] **Step 6: Verify the sender gate implementation matches the approved rule**

Keep the rule local and explicit:

```python
def _is_human_active_conversation(message: dict) -> bool:
    status = str(message.get("conversation_status") or "").upper()
    workflow = str(message.get("conversation_active_workflow") or "")
    if status == "HUMAN_ACTIVE":
        return True
    pending_ack = (
        status == "HANDOFF_REQUESTED"
        and workflow == "human_handoff"
        and (message.get("payload_json") or {}).get("handoff_ack") is True
    )
    return workflow == "human_handoff" and not pending_ack
```

- [ ] **Step 7: Write failing tests for failure audit and no state rollback**

Add worker tests for missing/permanently failed ack:

```python
def test_handoff_ack_dependency_failure_records_audit_and_preserves_requested_state():
    result = asyncio.run(process_pending_commands(
        repository,
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        execute_human_handoff=True,
        settings=settings,
    ))
    assert result[0]["status"] == "FAILED_DEPENDENCY"
    assert conversation_repository.failures[0][0] == command["conversation_id"]
    assert conversation_repository.failures[0][1]["stage"] == "handoff_ack"
    assert conversation_repository.current_status == "HANDOFF_REQUESTED"
    assert sender_client.transfers == []
```

- [ ] **Step 8: Run the failure-audit test and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/test_external_command_worker.py -q -k preserves_requested_state
```

Expected: FAIL because the dependency helper currently marks only the command and does not persist conversation failure audit.

- [ ] **Step 9: Implement failure audit with the existing `handoff_state` JSON**

Add a repository method that locks and merges the JSON while preserving status:

```python
async def record_handoff_failure(self, conversation_id: str, failure: dict) -> bool:
    async with self.pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT status, handoff_state FROM conversation_states WHERE conversation_id = %s FOR UPDATE",
                    (conversation_id,),
                )
                row = await cur.fetchone()
                if not row:
                    await conn.rollback()
                    return False
                handoff_state = json_loads(row.get("handoff_state")) or {}
                handoff_state["failure"] = failure
                await cur.execute(
                    "UPDATE conversation_states SET status = 'HANDOFF_REQUESTED', active_workflow = 'human_handoff', "
                    "workflow_stage = 'handoff_requested', handoff_state = %s WHERE conversation_id = %s "
                    "AND status <> 'HUMAN_ACTIVE'",
                    (json_dumps(handoff_state), conversation_id),
                )
            await conn.commit()
            return True
        except Exception:
            await conn.rollback()
            raise
```

Pass `conversation_repository` to `_handoff_ack_dependency_status`. On permanent dependency failure, write:

```python
await conversation_repository.record_handoff_failure(
    command["conversation_id"],
    {
        "stage": "handoff_ack",
        "command_id": command["id"],
        "outbound_message_id": ack.get("id") if ack else None,
        "status": HANDOFF_ACK_FAILED_STATUS,
        "error": reason,
        "recorded_at": datetime.now(UTC).isoformat(),
    },
)
```

- [ ] **Step 10: Run Task 1 tests and verify GREEN**

Run:

```bash
uv run --group dev pytest \
  tests/unit/test_external_result_consumer.py \
  tests/unit/test_sender_worker.py \
  tests/unit/test_external_command_worker.py \
  tests/unit/test_repositories.py -q
```

Expected: all selected files pass with no warnings or errors.

- [ ] **Step 11: Checkpoint Task 1 safely**

Run:

```bash
git diff --check -- src/app/workers/external_result_consumer.py src/app/workers/sender_worker.py src/app/workers/external_command_worker.py src/app/db/repositories.py tests/unit/test_external_result_consumer.py tests/unit/test_sender_worker.py tests/unit/test_external_command_worker.py tests/unit/test_repositories.py
```

If these files contain unrelated pre-existing hunks, do not commit them. Otherwise stage only these paths and commit with `fix: close LiveChat handoff ack boundary`.

---

### Task 2: Persist Backend Conclusions and Escalate the Second Dispute

**Files:**
- Create: `src/app/workflows/backend_dispute_escalation.py`
- Modify: `src/app/workers/external_result_consumer.py:320-410`
- Modify: `src/app/graph/nodes.py:270-590, 870-925`
- Test: `tests/unit/test_backend_dispute_escalation.py`
- Test: `tests/unit/graph/test_nodes.py`
- Test: `tests/unit/test_external_result_consumer.py`

**Interfaces:**
- Produces: `backend_conclusion_record(result_json: dict) -> dict`, `evaluate_backend_dispute(state: dict) -> dict | None`, and `clear_backend_dispute_memory(slot_memory: dict) -> dict`.
- Persists: `slot_memory.backend_conclusion = {intent, fingerprint, reply_intent, recorded_at}` plus `backend_dispute_count` and `backend_dispute_last_event_id`.

- [ ] **Step 1: Write failing pure-function tests for conclusion fingerprints**

```python
def test_backend_conclusion_fingerprint_is_stable_for_same_business_facts():
    first = backend_conclusion_record({
        "intent": "withdrawal_blocked_or_rollover",
        "reply_intent": "backend_turnover_remaining",
        "reply_facts": {"remaining_turnover": "18.88"},
    })
    second = backend_conclusion_record({
        "reply_facts": {"remaining_turnover": "18.88"},
        "reply_intent": "backend_turnover_remaining",
        "intent": "withdrawal_blocked_or_rollover",
    })
    assert first["fingerprint"] == second["fingerprint"]

def test_changed_backend_fact_changes_fingerprint():
    assert record("18.88")["fingerprint"] != record("0.00")["fingerprint"]
```

- [ ] **Step 2: Run fingerprint tests and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/test_backend_dispute_escalation.py -q -k fingerprint
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement canonical conclusion records**

```python
def backend_conclusion_record(result_json: dict) -> dict:
    canonical = {
        "intent": str(result_json.get("intent") or ""),
        "reply_intent": str(result_json.get("reply_intent") or ""),
        "reply_facts": result_json.get("reply_facts") if isinstance(result_json.get("reply_facts"), dict) else {},
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**canonical, "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
```

- [ ] **Step 4: Write failing tests for the two-dispute state machine**

```python
def test_same_backend_conclusion_handoffs_on_second_distinct_dispute_event():
    base = state_with_conclusion("fp-18.88")
    first = evaluate_backend_dispute(base | {
        "inbound_event_id": 101,
        "raw_user_input": "Ya intenté retirar cuatro veces y siempre lo devuelven",
    })
    second = evaluate_backend_dispute(first["state"] | {
        "inbound_event_id": 102,
        "raw_user_input": "Siempre me dicen que juegue y el retiro vuelve a fallar",
    })
    assert first["count"] == 1
    assert first["should_handoff"] is False
    assert second["count"] == 2
    assert second["should_handoff"] is True

def test_duplicate_event_does_not_increment_backend_dispute():
    first = evaluate_backend_dispute(state_for_event(101))
    duplicate = evaluate_backend_dispute(first["state"] | {"inbound_event_id": 101})
    assert duplicate["count"] == 1
```

- [ ] **Step 5: Run dispute tests and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/test_backend_dispute_escalation.py -q -k dispute
```

Expected: FAIL because the state machine is not implemented.

- [ ] **Step 6: Implement the deterministic dispute evaluator**

Use structured router signals first and a narrow multilingual fallback:

```python
DISPUTE_RE = re.compile(
    r"(otra vez|siempre|cuatro veces|lo devuelven|retiro fallido|sigue fallando|"
    r"still failed|same problem|again|not correct|还是失败|仍然不行|又失败|不对)",
    re.I,
)

def evaluate_backend_dispute(state: dict) -> dict | None:
    memory = dict(state.get("slot_memory") or {})
    conclusion = memory.get("backend_conclusion")
    if not isinstance(conclusion, dict) or not conclusion.get("fingerprint"):
        return None
    text = str(state.get("raw_user_input") or state.get("rewritten_question") or "")
    intent_result = state.get("intent_result") if isinstance(state.get("intent_result"), dict) else {}
    structured = intent_result.get("emotion") == "frustrated" or intent_result.get("risk_level") == "elevated"
    if not structured and not DISPUTE_RE.search(text):
        return None
    event_id = state.get("inbound_event_id") or state.get("event_id")
    if event_id is not None and str(memory.get("backend_dispute_last_event_id")) == str(event_id):
        count = int(memory.get("backend_dispute_count") or 0)
    else:
        count = int(memory.get("backend_dispute_count") or 0) + 1
        memory["backend_dispute_count"] = count
        memory["backend_dispute_last_event_id"] = event_id
    return {"state": {**state, "slot_memory": memory}, "count": count, "should_handoff": count >= 2}
```

- [ ] **Step 7: Write failing router tests for first-dispute SOP and second-dispute handoff**

```python
def test_router_requeries_after_first_backend_dispute_and_handoffs_after_second():
    first = intent_router_node(make_backend_dispute_state(event_id=101, count=0))
    assert first["route"] == "sop"
    assert first["slot_memory"]["backend_dispute_count"] == 1

    second = intent_router_node(make_backend_dispute_state(event_id=102, count=1))
    assert second["route"] == "human_handoff"
    assert second["intent_result"]["intent"] == "backend_conclusion_disputed_repeated"
```

- [ ] **Step 8: Run router tests and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/graph/test_nodes.py -q -k backend_dispute
```

Expected: FAIL because the router has no backend-conclusion dispute guard.

- [ ] **Step 9: Add the guard before both deterministic and LLM routing**

The guard must return a concrete route so updated counters persist:

```python
def _recent_backend_result_dispute_route(state: GraphState) -> GraphState | None:
    evaluation = evaluate_backend_dispute(state)
    if not evaluation:
        return None
    next_state = evaluation["state"]
    conclusion = next_state["slot_memory"]["backend_conclusion"]
    if evaluation["should_handoff"]:
        return _with_route(
            next_state,
            "backend_conclusion_disputed_repeated",
            "human_handoff",
            "Customer disputed the same backend conclusion twice.",
            confidence=0.99,
        )
    return _with_route(
        next_state,
        str(conclusion.get("intent") or "withdrawal_blocked_or_rollover"),
        "sop",
        "Customer disputed the latest backend conclusion once; recheck before escalation.",
        confidence=0.95,
        sop_name=str(conclusion.get("intent") or "withdrawal_blocked_or_rollover"),
    )
```

Call it before LLM authority checks in both `intent_router_node` and the node returned by `make_intent_router_node`.

- [ ] **Step 10: Write failing reset tests**

```python
@pytest.mark.parametrize("text", ["gracias", "listo", "问题解决了"])
def test_acceptance_clears_backend_dispute_memory(text):
    routed = intent_router_node(state_with_count(text, count=1))
    assert "backend_dispute_count" not in routed["slot_memory"]

def test_changed_backend_conclusion_clears_dispute_count():
    handler = build_result_handler(result_row(remaining="0.00"), conversation=conversation_with("18.88", count=1))
    assert handler["graph_state"]["slot_memory"]["backend_dispute_count"] == 0
```

- [ ] **Step 11: Run reset tests and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/graph/test_nodes.py tests/unit/test_external_result_consumer.py -q -k 'acceptance_clears or changed_backend_conclusion'
```

Expected: FAIL because reset behavior is missing.

- [ ] **Step 12: Persist conclusions and reset only on accepted resolution, route change, or changed facts**

In the result consumer, merge the new conclusion into `graph_state.slot_memory`. Preserve the counter when the fingerprint is unchanged and reset it when changed:

```python
previous = (conversation or {}).get("slot_memory") or {}
record = backend_conclusion_record(result_json)
same = (previous.get("backend_conclusion") or {}).get("fingerprint") == record["fingerprint"]
slot_updates = {
    "backend_conclusion": record,
    "backend_dispute_count": int(previous.get("backend_dispute_count") or 0) if same else 0,
}
```

For acceptance or a different routed business intent, remove:

```python
for key in ("backend_dispute_count", "backend_dispute_last_event_id"):
    slot_memory.pop(key, None)
```

- [ ] **Step 13: Run Task 2 tests and verify GREEN**

Run:

```bash
uv run --group dev pytest \
  tests/unit/test_backend_dispute_escalation.py \
  tests/unit/graph/test_nodes.py \
  tests/unit/test_external_result_consumer.py -q
```

Expected: all selected files pass.

- [ ] **Step 14: Checkpoint Task 2 safely**

Run `git diff --check` for Task 2 paths. Commit only if no unrelated pre-existing hunks would be included; otherwise retain the verified working-tree changes and report why no commit was made.

---

### Task 3: Enforce Trusted Identity Values End to End

**Files:**
- Modify: `src/app/workflows/llm_sop_dialogue_planner.py:330-405`
- Modify: `src/app/workflows/sop_handlers.py:130-270`
- Modify: `src/app/workers/external_command_worker.py:940-980`
- Modify: `src/app/workers/external_result_consumer.py:600-680`
- Test: `tests/unit/test_graph_sop_dialogue_planner.py`
- Test: `tests/unit/workflows/test_sop_handlers.py`
- Test: `tests/unit/test_external_command_worker.py`
- Test: `tests/unit/test_external_result_consumer.py`

**Interfaces:**
- Consumes and propagates `identity_source` with allowed trusted values `user_text` and `confirmed_by_user`.
- Produces backend results that retain `identity_source`; only trusted sources affect repeated not-found escalation.

- [ ] **Step 1: Write failing tests for date, amount, and image-derived identity rejection**

```python
@pytest.mark.parametrize("candidate", ["2026-07-14", "18.88", "60000"])
def test_untrusted_numeric_candidate_does_not_replace_confirmed_identity(candidate):
    result = plan_sop_dialogue_from_state({
        "raw_user_input": "retiro fallido",
        "slot_memory": {"account_or_phone": "trusted-user", "identity_source": "user_text"},
        "llm_sop_slot_result": {"status": "accepted", "slot_updates": {"account_or_phone": candidate}},
    }, "withdrawal_blocked_or_rollover")
    assert result["slot_memory"]["account_or_phone"] == "trusted-user"
    assert result["slot_memory"]["identity_source"] == "user_text"
```

- [ ] **Step 2: Run identity overwrite tests and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/test_graph_sop_dialogue_planner.py -q -k untrusted_numeric_candidate
```

Expected: FAIL if any untrusted candidate overwrites the trusted value.

- [ ] **Step 3: Implement source-aware identity merging**

Keep the existing trusted identity unless the new value is visibly present in raw user text or explicitly confirmed:

```python
if key in IDENTITY_SLOT_KEYS and not _identity_update_has_user_text_evidence(key, value, state):
    _record_identity_hint(slot_memory, key, value)
    continue
slot_memory[key] = value
slot_memory["identity_source"] = "user_text"
```

- [ ] **Step 4: Write failing command/result propagation test**

```python
def test_backend_result_preserves_identity_source_from_command():
    result = build_mock_or_real_backend_result({
        "account_or_phone": "trusted-user",
        "identity_source": "user_text",
    })
    assert result["identity_source"] == "user_text"
```

- [ ] **Step 5: Run propagation test and verify RED**

Run:

```bash
uv run --group dev pytest tests/unit/test_external_command_worker.py -q -k preserves_identity_source
```

Expected: FAIL if `_copy_user_visible_context_to_result` drops the source.

- [ ] **Step 6: Propagate identity source and gate escalation**

Include `identity_source` in command payloads and copied result context. In `_backend_lookup_escalation`, return without incrementing when `kind == "not_found"` and the source is not trusted:

```python
if kind == "not_found" and identity_source not in {"user_text", "confirmed_by_user"}:
    return {
        "should_handoff": False,
        "slot_memory": existing_lookup_memory(slot_memory),
        "external_commands": [],
    }
```

- [ ] **Step 7: Run Task 3 tests and verify GREEN**

Run:

```bash
uv run --group dev pytest \
  tests/unit/test_graph_sop_dialogue_planner.py \
  tests/unit/workflows/test_sop_handlers.py \
  tests/unit/test_external_command_worker.py \
  tests/unit/test_external_result_consumer.py -q
```

Expected: all selected files pass.

- [ ] **Step 8: Checkpoint Task 3 safely**

Run `git diff --check` on Task 3 paths. Commit only cleanly isolated work; otherwise leave verified changes unstaged and document the overlap.

---

### Task 4: Add MySQL Transaction, Replay, and Worker Closed-Loop Tests

**Files:**
- Create: `tests/fixtures/replay/livechat_repeated_backend_dispute_es.json`
- Create: `tests/integration/test_livechat_handoff_closed_loop_mysql.py`
- Modify: `tests/integration/test_db_replay_runner.py`
- Test: `tests/unit/test_livechat_idle_timer.py`

**Interfaces:**
- Uses: `provision_mysql_test_settings`, `create_bootstrapped_mysql_pool`, `GatewayService`, repository classes, `process_pending_results`, `process_pending_message`, and `process_pending_commands`.
- Produces: a deterministic test proving state/outbox/command atomicity, second-dispute escalation, ack-before-transfer ordering, and idle-timer silence.

- [ ] **Step 1: Add the sanitized Spanish replay fixture**

```json
{
  "name": "livechat_repeated_backend_dispute_es",
  "identity": "test-player-3043",
  "backend_result": {
    "intent": "withdrawal_blocked_or_rollover",
    "reply_intent": "backend_turnover_remaining",
    "reply_facts": {"remaining_turnover": "18.88"},
    "identity_source": "user_text"
  },
  "messages": [
    "Por qué me rechazan los retiros",
    "test-player-3043",
    "Ya intenté retirar cuatro veces y siempre lo devuelven",
    "Siempre me dicen que juegue y después aparece retiro fallido"
  ],
  "expected_status": "HANDOFF_REQUESTED",
  "expected_handoff_commands": 1,
  "expected_handoff_acks": 1
}
```

- [ ] **Step 2: Write the failing MySQL replay test**

The test provisions only `ai_customer_service_test`, drives each event with a unique dedup key, inserts the deterministic backend result after the identity event, then asserts:

```python
conversation = await fetch_conversation(pool, chat_id)
assert conversation["status"] == "HANDOFF_REQUESTED"
assert conversation["active_workflow"] == "human_handoff"
assert await count_handoff_commands(pool, conversation["conversation_id"]) == 1
assert await count_handoff_acks(pool, conversation["conversation_id"]) == 1
```

- [ ] **Step 3: Run the MySQL replay test and verify RED**

Run:

```bash
uv run --group dev pytest tests/integration/test_livechat_handoff_closed_loop_mysql.py -q -k repeated_backend_dispute
```

Expected: FAIL before the Task 2 behavior is implemented. If no local test MySQL is configured, record `NOT RUN: MYSQL_TEST_DSN unavailable`; do not point this command at production.

- [ ] **Step 4: Add the ack-before-transfer worker closure test**

Use a fake sender that appends call names to one list:

```python
calls = []

class FakeLiveChatClient:
    async def send_text(self, **kwargs):
        calls.append("ack")
        return {"event_id": "ack-event"}

    async def transfer_chat_to_group(self, *args, **kwargs):
        calls.append("transfer")
        return {"ok": True}

assert calls == ["ack", "transfer"]
assert updated_conversation["status"] == "HUMAN_ACTIVE"
```

Process the ack outbox first, then the external command. Re-run command processing and assert `calls.count("transfer") == 1`.

- [ ] **Step 5: Add transaction rollback and idle-timer tests**

Inject an exception on external command insert and assert no state or ack row commits. Add/retain parameterized idle tests proving both `HANDOFF_REQUESTED` and `HUMAN_ACTIVE` are excluded from follow-up and close operations.

- [ ] **Step 6: Run Task 4 tests and verify GREEN**

Run:

```bash
uv run --group dev pytest tests/unit/test_livechat_idle_timer.py -q
```

Then, only with a local test DSN:

```bash
uv run --group dev pytest \
  tests/integration/test_livechat_handoff_closed_loop_mysql.py \
  tests/integration/test_db_replay_runner.py -q
```

Expected: unit tests pass; configured MySQL tests pass without touching any non-test database.

- [ ] **Step 7: Checkpoint Task 4 safely**

New fixture and integration files may be staged independently. Do not stage unrelated modifications from shared test files. Commit isolated additions with `test: cover LiveChat handoff closed loop` when safe.

---

### Task 5: Full Regression and Production Read-Only Audit

**Files:**
- Verify only; no production code changes are permitted in this task.

**Interfaces:**
- Consumes all Task 1-4 behavior.
- Produces a verification report that separates unit, local MySQL integration, replay, and production read-only evidence.

- [ ] **Step 1: Run the complete unit suite**

```bash
uv run --group dev pytest tests/unit -q
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the complete configured integration suite**

Only when `MYSQL_TEST_DSN` names `ai_customer_service_test`:

```bash
uv run --group dev pytest tests/integration -q
```

Expected: zero failures and zero errors. Otherwise report the suite as not run and name the missing safe prerequisite.

- [ ] **Step 3: Run formatting and diff checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; status lists only intentional changes plus preserved pre-existing work.

- [ ] **Step 4: Run the production read-only handoff audit**

Query recent `human_handoff.requested` records and correlate them to ack rows and transfer results. Report counts for:

```text
missing_ack
ack_not_sent
failed_dependency
transferred
duplicate_transfer_results
```

This query must use `SELECT` only and must not lease, update, retry, or repair any production record.

- [ ] **Step 5: Re-read the design completion criteria**

Confirm each item in `docs/superpowers/specs/2026-07-15-livechat-handoff-closed-loop-hardening-design.md` with a test name and fresh command output. If any criterion lacks evidence, keep the task incomplete.

- [ ] **Step 6: Prepare the handoff summary**

Report:

```text
Implemented behavior
Focused test result
Full unit result
MySQL integration result or exact reason not run
Production read-only audit result
Pre-existing workspace changes preserved
Deployment status (not deployed unless an explicit deployment was performed)
```
