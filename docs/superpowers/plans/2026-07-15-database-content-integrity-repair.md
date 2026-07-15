# Database Content Integrity Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve customer history during HUMAN_ACTIVE while keeping automation silent, and guarantee timestamps on newly inserted PROCESSED external-command results.

**Architecture:** Build customer history independently from graph execution, then persist it inside the existing gateway transaction even on HUMAN_ACTIVE early-return paths. Enforce the processed-result timestamp invariant in the result repository using the database clock.

**Tech Stack:** Python 3.13, aiomysql, MySQL/MariaDB, pytest.

## Global Constraints

- No schema migration.
- No production-data backfill.
- No orphan cleanup or foreign keys.
- HUMAN_ACTIVE must not create AI, outbound, or external-command work.
- Preserve unrelated dirty-worktree changes.
- The implementation files already contain unrelated user changes; do not stage or commit whole files as part of this task.

---

### Task 1: Preserve HUMAN_ACTIVE customer history

**Files:**
- Modify: `src/app/services/gateway.py:280`
- Modify: `src/app/db/repositories.py:2737`
- Test: `tests/unit/test_gateway.py:1852`
- Test: `tests/unit/test_repositories.py:2406`

**Interfaces:**
- Consumes: `build_customer_message_from_inbound(event, conversation, inbound_event_id)` and `ConversationMessageRepository.insert_idempotent_on_connection(conn, message)`.
- Produces: a customer `conversation_messages` row for accepted MESSAGE/FILE events, including HUMAN_ACTIVE and takeover-race paths.

- [ ] **Step 1: Change the service test to require customer history**

Update `test_gateway_service_human_active_records_inbound_but_does_not_run_graph_or_enqueue_work`:

```python
assert len(message_repository.inserted) == 1
assert message_repository.inserted[0]["sender_role"] == "customer"
assert message_repository.inserted[0]["text_content"] == "are you there?"
assert inbound_repository.processed == [15]
```

- [ ] **Step 2: Change transaction tests to require only the customer insert**

In both HUMAN_ACTIVE and takeover-race repository tests, use a dedicated message repository that records `insert_customer_message`, keep business repositories recording `insert_business_work`, and assert:

```python
assert calls == ["get_conversation", "insert_customer_message", "mark_processed"]
assert result["message_insert"] == {"inserted": True}
assert result["outbound_inserts"] == []
assert result["external_command_inserts"] == []
```

For the race test, include `"update_state_rejected"` before `"insert_customer_message"`.

- [ ] **Step 3: Run focused tests and verify they fail**

Run:

```bash
uv run --group dev pytest \
  tests/unit/test_gateway.py::test_gateway_service_human_active_records_inbound_but_does_not_run_graph_or_enqueue_work \
  tests/unit/test_repositories.py::test_gateway_transaction_discards_stale_graph_work_after_human_takeover \
  tests/unit/test_repositories.py::test_gateway_transaction_discards_work_when_human_takeover_wins_state_update_race -q
```

Expected: failures showing no customer message was inserted.

- [ ] **Step 4: Build customer history independently of graph state**

In `GatewayService.process_event()` use the accepted event itself as the condition:

```python
customer_message = (
    build_customer_message_from_inbound(event, conversation, inbound_event_id)
    if (
        not event.ignored
        and event.standard_event_type in {"MESSAGE_CREATED", "FILE_RECEIVED"}
    )
    else None
)
```

- [ ] **Step 5: Insert customer history before HUMAN_ACTIVE early returns**

In each of the two early-return branches in `GatewayTransactionRepository.process_event_transactionally()`, insert only the passed customer message before marking inbound processed:

```python
message_insert = None
if customer_message is not None:
    customer_message["conversation_id"] = conversation["conversation_id"]
    message_insert = await self.conversation_message_repository.insert_idempotent_on_connection(
        conn,
        customer_message,
    )
await self.inbound_repository.mark_processed_on_connection(conn, inbound_event_id)
await conn.commit()
```

Return `message_insert` instead of `None`. Do not move assistant, outbound, command, or graph writes into these branches.

- [ ] **Step 6: Re-run focused tests**

Run the command from Step 3.

Expected: all three tests pass.

- [ ] **Step 7: Run the two affected modules**

Run:

```bash
uv run --group dev pytest tests/unit/test_gateway.py tests/unit/test_repositories.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Review the scoped diff**

```bash
git diff -- src/app/services/gateway.py src/app/db/repositories.py tests/unit/test_gateway.py tests/unit/test_repositories.py
```

Expected: only the intended new hunks plus the user's pre-existing changes. Do not stage the whole files.

---

### Task 2: Timestamp inserted PROCESSED results

**Files:**
- Modify: `src/app/db/repositories.py:1388`
- Test: `tests/unit/test_repositories.py:2164`

**Interfaces:**
- Consumes: `ExternalCommandResultRepository.insert_idempotent_on_connection(conn, result)`.
- Produces: database-managed `processed_at` for a missing timestamp when the inserted status is PROCESSED.

- [ ] **Step 1: Add three repository tests**

Extend the insertion helper so it accepts overrides, then add:

```python
def test_external_command_result_insert_sets_database_time_for_processed_status():
    result, cursor = asyncio.run(
        run_external_result_insert_idempotent(rowcount=1, status="PROCESSED")
    )
    assert "CASE WHEN %s = 'PROCESSED' THEN NOW(6)" in cursor.sql
    assert cursor.args[9] == "PROCESSED"
    assert cursor.args[10] is None
    assert cursor.args[11] == "PROCESSED"


def test_external_command_result_insert_preserves_explicit_processed_at():
    processed_at = "2026-07-15 01:02:03.000000"
    _, cursor = asyncio.run(
        run_external_result_insert_idempotent(
            rowcount=1,
            status="PROCESSED",
            processed_at=processed_at,
        )
    )
    assert cursor.args[10] == processed_at


def test_external_command_result_insert_keeps_pending_processed_at_null():
    _, cursor = asyncio.run(
        run_external_result_insert_idempotent(rowcount=1, status="PENDING")
    )
    assert cursor.args[9] == "PENDING"
    assert cursor.args[10] is None
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run --group dev pytest tests/unit/test_repositories.py -k "external_command_result_insert" -q
```

Expected: the new SQL invariant assertion fails.

- [ ] **Step 3: Enforce the invariant in the insert SQL**

Compute the status once and change the processed timestamp value expression:

```python
status = result.get("status") or "PENDING"
```

```sql
%s, %s, %s, %s, %s,
%s, %s, %s, %s, %s,
COALESCE(%s, CASE WHEN %s = 'PROCESSED' THEN NOW(6) ELSE NULL END), %s, %s
```

Pass both `result.get("processed_at")` and `status` for the timestamp expression. Leave `ON DUPLICATE KEY UPDATE id = id` unchanged.

- [ ] **Step 4: Re-run repository tests**

Run:

```bash
uv run --group dev pytest tests/unit/test_repositories.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Review the scoped diff**

```bash
git diff -- src/app/db/repositories.py tests/unit/test_repositories.py
```

Expected: the result timestamp invariant and its tests are present; unrelated user changes remain untouched. Do not stage the whole files.

---

### Task 3: Final verification

**Files:**
- Verify only; no planned file changes.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: fresh evidence that the complete unit suite passes and the scoped diff is clean.

- [ ] **Step 1: Run the complete unit suite**

```bash
uv run --group dev pytest tests/unit -q
```

Expected: all tests pass.

- [ ] **Step 2: Check formatting and scope**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; unrelated dirty-worktree changes remain untouched.

- [ ] **Step 3: Re-run read-only production checks after deployment**

Verify a newly created controlled HUMAN_ACTIVE inbound has one matching customer message and no automated work, and a newly created PROCESSED handoff result has non-null `processed_at`. Do not mutate production data as part of this local implementation task.
