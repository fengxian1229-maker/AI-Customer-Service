# Telegram Cross-Thread Money Case Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Send one immediate English Telegram follow-up reply to the original deposit/withdrawal case when the customer asks again in a new LiveChat thread, while preserving case identity, completion state, supplements, and reply routing.

**Architecture:** Add a small durable follow-up record beside the existing Telegram case tables, enrich graph state with candidate money cases, and emit a dedicated telegram.remind_case command. The Telegram worker reserves the follow-up number, renders a deterministic English card with an optional validated LLM customer summary, sends a new reply to the original root message, and records the message so staff replies route to the latest LiveChat thread.

**Tech Stack:** Python 3.12, asyncio, LangGraph state dictionaries, aiomysql/MySQL 8, Telegram Bot API, pytest, existing Gemini chat-model adapter.

## Global Constraints

- Support exactly deposit_missing and withdrawal_missing.
- The first qualifying message in a new thread triggers immediately; the same Telegram case and LiveChat thread may create at most one reminder, even if the reminder kind changes.
- Telegram reminder text, labels, and customer update must be English; LiveChat replies remain in the customer's current language.
- Case type, follow-up number, previous status, and action text are deterministic. LLM output may populate only Customer Update.
- LLM failure, non-English output, changed critical facts, or output over 300 characters uses a fixed English fallback and must not block sending.
- Reminders use sendMessage with reply_to_message_id=root_message_id and never editMessageText.
- A status follow-up plus new order data or screenshots creates one reminder command, not a second telegram.append_to_case.
- Multiple plausible cases without an exact transaction/root match produce clarification and no Telegram command.
- completed_by_staff plus an explicit still-not-received claim becomes completion_disputed; unknown staff text never defaults to completed.
- Preserve all unrelated dirty-worktree changes. Inspect the current file diff before every edit. New files may be staged directly; existing dirty files must be staged with git add -p followed by a complete git diff --cached review.
- Design source: docs/superpowers/specs/2026-07-15-telegram-cross-thread-money-case-follow-up-design.md.

---

## File Map

New files:

- sql/019_telegram_case_followups.sql — durable reservation, numbering, delivery state, and once-per-thread constraint.
- src/app/services/telegram_case_status.py — deterministic money-case lifecycle classification.
- src/app/services/telegram_case_followup.py — candidate resolution, English summary validation/fallback, and reminder rendering.
- tests/unit/test_telegram_case_status.py
- tests/unit/test_telegram_case_followup.py

Existing files:

- sql/014_telegram_cases.sql and src/app/db/bootstrap.py — current route fields and compatibility.
- src/app/db/telegram_repositories.py and src/app/db/repositories.py — candidates, reservations, status, current routing, transactional updates.
- src/app/graph/state.py, src/app/graph/nodes.py, src/app/services/gateway.py — candidate enrichment and deterministic routing.
- src/app/services/outbox.py, src/app/workflows/command_contracts.py, src/app/workflows/sop_command_builder.py, src/app/workflows/waiting_backend_classifier.py — command generation and one-per-thread dedup.
- src/app/services/telegram_case_card.py, src/app/channels/telegram/sender_client.py — English message model and reply-to-root delivery.
- src/app/workers/external_command_worker.py, src/app/workers/telegram_reply_consumer.py, src/app/workers/service_runner.py — execution, authoritative status, and dependency wiring.
- Matching tests under tests/unit, tests/unit/graph, and tests/unit/workflows.

---

### Task 1: Persist Follow-Ups and Current Reply Routing

**Files:**
- Create: sql/019_telegram_case_followups.sql
- Modify: sql/014_telegram_cases.sql
- Modify: src/app/db/bootstrap.py
- Modify: src/app/db/telegram_repositories.py
- Test: tests/unit/test_bootstrap.py
- Test: tests/unit/test_telegram_case_repository.py

**Interfaces:**
- Produces: TelegramCaseRepository.list_money_case_candidates(tenant_id: str, chat_id: str, source_thread_id: str) -> list[dict]
- Produces: TelegramCaseRepository.reserve_followup(external_command_id: int, telegram_case_id: int, source_conversation_id: str, source_thread_id: str, follow_up_kind: str, previous_status: str) -> dict
- Produces: TelegramCaseRepository.record_followup_sent(followup_id: int, telegram_message_id: int, attachment_message_ids: list[int], customer_update_en: str) -> None
- Produces: TelegramCaseRepository.mark_followup_delivery_uncertain(followup_id: int, error: str) -> None
- Produces: TelegramCaseRepository.update_case_status_on_connection(conn, telegram_case_id: int, status: str) -> None

- [ ] **Step 1: Write failing schema and repository tests**

~~~python
def test_load_sql_files_includes_telegram_case_followups():
    files = load_sql_files(Path("sql"))
    assert files[-1].name == "019_telegram_case_followups.sql"


def test_followup_schema_is_once_per_case_and_thread():
    ddl = Path("sql/019_telegram_case_followups.sql").read_text()
    assert "UNIQUE KEY uk_telegram_case_followups_case_thread" in ddl
    assert "(telegram_case_id, source_thread_id)" in ddl
    assert "UNIQUE KEY uk_telegram_case_followups_case_number" in ddl


def test_case_candidate_query_is_money_and_chat_scoped():
    repository, cursor = repository_with_rows([])
    asyncio.run(repository.list_money_case_candidates("tenant-a", "chat-1", "thread-2"))
    assert "c.intent IN ('deposit_missing', 'withdrawal_missing')" in cursor.sql
    assert cursor.args == ("tenant-a", "chat-1", "thread-2")


def test_replaying_case_created_does_not_reopen_completed_case():
    repository, cursor = repository_with_case_status("completed_by_staff")
    asyncio.run(repository.upsert_case_created(case_created_row(), case_created_result()))
    assert "WHEN status = 'created' THEN 'awaiting_review'" in cursor.sql
    assert "status = VALUES(status)" not in cursor.sql
~~~

- [ ] **Step 2: Run the tests and confirm red**

~~~bash
uv run --group dev pytest tests/unit/test_bootstrap.py::test_load_sql_files_includes_telegram_case_followups tests/unit/test_telegram_case_repository.py -q
~~~

Expected: FAIL because SQL 019 and repository APIs do not exist.

- [ ] **Step 3: Add schema and compatibility**

Create SQL 019:

~~~sql
CREATE TABLE IF NOT EXISTS telegram_case_followups (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  telegram_case_id BIGINT UNSIGNED NOT NULL,
  external_command_id BIGINT UNSIGNED NOT NULL,
  source_conversation_id VARCHAR(128) NOT NULL,
  source_thread_id VARCHAR(128) NOT NULL,
  follow_up_kind VARCHAR(64) NOT NULL,
  follow_up_number INT NOT NULL,
  customer_update_en VARCHAR(300) NULL,
  previous_status VARCHAR(64) NOT NULL,
  telegram_message_id BIGINT NULL,
  status VARCHAR(64) NOT NULL DEFAULT 'reserved',
  last_error TEXT NULL,
  sent_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_telegram_case_followups_case_thread (telegram_case_id, source_thread_id),
  UNIQUE KEY uk_telegram_case_followups_case_number (telegram_case_id, follow_up_number),
  UNIQUE KEY uk_telegram_case_followups_command (external_command_id),
  KEY idx_telegram_case_followups_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
~~~

Add current_conversation_id and current_thread_id to the base case DDL, default new cases to awaiting_review, and call this compatibility function from bootstrap_database:

~~~python
async def ensure_telegram_cases_compat(cur) -> None:
    await ensure_columns(cur, "telegram_cases", {
        "current_conversation_id": "ALTER TABLE telegram_cases ADD COLUMN current_conversation_id VARCHAR(128) NULL",
        "current_thread_id": "ALTER TABLE telegram_cases ADD COLUMN current_thread_id VARCHAR(128) NULL",
    })
~~~

Update TelegramCaseRepository._upsert_case_created_on_connection to insert awaiting_review. On duplicate sync, convert only legacy created to awaiting_review and preserve under_review, waiting_customer, completed_by_staff, completed_confirmed_by_customer, completion_disputed, and terminal_other. Candidate reads must exclude c.thread_id equal to source_thread_id.

- [ ] **Step 4: Implement atomic reservation**

Use SELECT FOR UPDATE. The original card counts as contact 1, so the first follow-up number is 2. Duplicate case/thread returns the existing record:

~~~python
async def reserve_followup(self, *, external_command_id: int, telegram_case_id: int,
                           source_conversation_id: str, source_thread_id: str,
                           follow_up_kind: str, previous_status: str) -> dict:
    if not source_thread_id:
        raise ValueError("source_thread_id is required")
    async with self.pool.acquire() as conn:
        await conn.begin()
        try:
            case = await self._lock_case_on_connection(conn, telegram_case_id)
            existing = await self._find_followup_on_connection(conn, telegram_case_id, source_thread_id)
            if existing:
                await conn.commit()
                return {**existing, "duplicate": True, "case": case}
            number = await self._next_followup_number_on_connection(conn, telegram_case_id)
            followup_id = await self._insert_followup_on_connection(
                conn, external_command_id, telegram_case_id, source_conversation_id,
                source_thread_id, follow_up_kind, number, previous_status,
            )
            await self._update_current_route_on_connection(
                conn, telegram_case_id, source_conversation_id, source_thread_id,
            )
            await conn.commit()
            return {
                "id": followup_id,
                "follow_up_number": number,
                "status": "reserved",
                "duplicate": False,
                "case": case,
            }
        except Exception:
            await conn.rollback()
            raise
~~~

- [ ] **Step 5: Run focused tests**

~~~bash
uv run --group dev pytest tests/unit/test_bootstrap.py tests/unit/test_telegram_case_repository.py -q
~~~

Expected: PASS.

- [ ] **Step 6: Commit only task-owned hunks**

~~~bash
git add sql/019_telegram_case_followups.sql
git add -p -- sql/014_telegram_cases.sql src/app/db/bootstrap.py src/app/db/telegram_repositories.py tests/unit/test_bootstrap.py tests/unit/test_telegram_case_repository.py
git diff --cached --check
git diff --cached
git commit -m "feat: persist Telegram case follow-ups"
~~~

---

### Task 2: Classify Authoritative Money-Case Status

**Files:**
- Create: src/app/services/telegram_case_status.py
- Create: tests/unit/test_telegram_case_status.py
- Modify: src/app/workers/telegram_reply_consumer.py
- Test: tests/unit/test_telegram_reply_consumer.py

**Interfaces:**
- Produces: classify_money_case_status(intent: str, raw_reply: str, current_status: str | None = None) -> str
- Produces: normalize_legacy_case_status(case: dict) -> str
- Consumes raw Telegram staff text, never polished LLM output.

- [ ] **Step 1: Write lifecycle tests**

~~~python
@pytest.mark.parametrize("intent,text", [
    ("deposit_missing", "Deposit has been credited successfully"),
    ("withdrawal_missing", "Withdrawal completed successfully"),
])
def test_matching_completion_is_completed_by_staff(intent, text):
    assert classify_money_case_status(intent, text) == "completed_by_staff"


@pytest.mark.parametrize("text", ["still checking", "under review", "processing, please wait"])
def test_wait_language_is_under_review(text):
    assert classify_money_case_status("withdrawal_missing", text) == "under_review"


def test_unknown_text_is_not_completed():
    assert classify_money_case_status("deposit_missing", "noted") == "under_review"


def test_opposite_transaction_completion_does_not_close_case():
    assert classify_money_case_status(
        "withdrawal_missing", "Deposit credited successfully"
    ) != "completed_by_staff"
~~~

- [ ] **Step 2: Run and confirm red**

~~~bash
uv run --group dev pytest tests/unit/test_telegram_case_status.py -q
~~~

Expected: FAIL on import.

- [ ] **Step 3: Implement deterministic precedence**

~~~python
MONEY_INTENTS = {"deposit_missing", "withdrawal_missing"}


def classify_money_case_status(intent: str, raw_reply: str, current_status: str | None = None) -> str:
    text = str(raw_reply or "").strip().lower()
    if intent not in MONEY_INTENTS:
        return current_status or "under_review"
    if ASK_CUSTOMER_PATTERN.search(text):
        return "waiting_customer"
    if WAIT_PATTERN.search(text):
        return "under_review"
    if _matching_completion(intent, text):
        return "completed_by_staff"
    if TERMINAL_OTHER_PATTERN.search(text):
        return "terminal_other"
    if current_status in {"completion_disputed", "completed_confirmed_by_customer"}:
        return current_status
    return "under_review"
~~~

normalize_legacy_case_status maps created lazily from conversation slots: validated telegram_case_resolved_at becomes completed_by_staff, last_telegram_staff_reply_type=long_wait becomes under_review, and no staff reply becomes awaiting_review.

- [ ] **Step 4: Wire staff replies to internal case updates**

Keep StaffReplyResult.type for customer copy. Add authoritative status separately:

~~~python
case_status = classify_money_case_status(
    active_workflow, raw_reply, result_json.get("telegram_case_status")
)
resolved["graph_state"]["telegram_case_update"] = {
    "telegram_case_id": result_json["telegram_case_id"],
    "status": case_status,
}
~~~

Add tests for long_wait, explicit completion, unknown resolution text, and opposite money intent.

- [ ] **Step 5: Run focused tests**

~~~bash
uv run --group dev pytest tests/unit/test_telegram_case_status.py tests/unit/test_telegram_reply_consumer.py -q
~~~

Expected: PASS.

- [ ] **Step 6: Commit only task-owned hunks**

~~~bash
git add src/app/services/telegram_case_status.py tests/unit/test_telegram_case_status.py
git add -p -- src/app/workers/telegram_reply_consumer.py tests/unit/test_telegram_reply_consumer.py
git diff --cached --check
git diff --cached
git commit -m "feat: track Telegram money case status"
~~~

---

### Task 3: Resolve Cross-Thread Cases and Emit One Reminder Command

**Files:**
- Create: src/app/services/telegram_case_followup.py
- Modify: src/app/graph/state.py
- Modify: src/app/graph/nodes.py
- Modify: src/app/services/gateway.py
- Modify: src/app/services/outbox.py
- Modify: src/app/workflows/command_contracts.py
- Modify: src/app/workflows/sop_command_builder.py
- Modify: src/app/workflows/waiting_backend_classifier.py
- Test: tests/unit/test_telegram_case_followup.py
- Test: tests/unit/graph/test_nodes.py
- Test: tests/unit/test_gateway.py
- Test: tests/unit/workflows/test_waiting_backend_classifier.py

**Interfaces:**
- Produces: resolve_money_case_followup(candidates: list[dict], text: str, inherited_root_message_id: int | None) -> dict
- Result shapes: {"status": "matched", "case": case}, {"status": "ambiguous"}, or {"status": "none"}.
- Produces: build_money_case_followup_dedup_key(case: dict, source_thread_id: str) -> str.
- Produces CommandType.TELEGRAM_REMIND_CASE.

- [ ] **Step 1: Write resolution and workflow tests**

~~~python
def test_inherited_root_wins_over_other_case():
    result = resolve_money_case_followup(
        [case(1, root=100), case(2, root=200)],
        text="why is it still not received",
        inherited_root_message_id=200,
    )
    assert result["status"] == "matched"
    assert result["case"]["id"] == 2


def test_multiple_cases_without_exact_match_are_ambiguous():
    result = resolve_money_case_followup(
        [case(1, root=100), case(2, root=200)],
        text="still not received",
        inherited_root_message_id=None,
    )
    assert result["status"] == "ambiguous"


def test_exact_order_id_wins_over_other_candidates():
    result = resolve_money_case_followup(
        [case(1, root=100, order_id="TX100"), case(2, root=200, order_id="TX200")],
        text="TX200 is still not received",
        inherited_root_message_id=None,
    )
    assert result["status"] == "matched"
    assert result["case"]["id"] == 2


def test_new_thread_followup_with_supplement_emits_remind_only():
    result = handle_waiting_backend(new_thread_waiting_state(with_supplement=True))
    assert [item["type"] for item in result["commands"]] == [CommandType.TELEGRAM_REMIND_CASE]
    assert result["commands"][0]["payload"]["supplement"]["attachment_urls"] == ["https://cdn/new.png"]
~~~

- [ ] **Step 2: Run and confirm red**

~~~bash
uv run --group dev pytest tests/unit/test_telegram_case_followup.py tests/unit/graph/test_nodes.py tests/unit/workflows/test_waiting_backend_classifier.py -q
~~~

Expected: FAIL because resolver, graph candidates, and command type do not exist.

- [ ] **Step 3: Load candidates before graph execution**

Inject TelegramCaseRepository into GatewayService and enrich new-thread customer events:

~~~python
async def _with_money_case_candidates(self, conversation: dict, event: InboundEvent) -> dict:
    if not event.thread_id or not self.telegram_case_repository:
        return conversation
    candidates = await self.telegram_case_repository.list_money_case_candidates(
        tenant_id=conversation.get("tenant_id") or "default",
        chat_id=event.chat_id or "unknown",
        source_thread_id=event.thread_id,
    )
    return {**conversation, "telegram_money_case_candidates": candidates}
~~~

Copy telegram_money_case_candidates into GraphState in build_graph_state_from_event. Keep it internal.

- [ ] **Step 4: Add deterministic route selection**

Before generic money-intent routing:

~~~python
followup = resolve_money_case_followup(
    state.get("telegram_money_case_candidates") or [],
    text=text,
    inherited_root_message_id=(state.get("slot_memory") or {}).get("telegram_message_id"),
)
if followup["status"] == "ambiguous":
    return _money_case_clarification_route(state)
if followup["status"] == "matched":
    return _money_case_followup_route(state, followup["case"])
~~~

Exact order/transaction ID and inherited root win. One eligible case auto-matches. completed_by_staff requires an explicit still-not-received dispute. waiting_customer stays on the missing-data path.

- [ ] **Step 5: Preserve custom command dedup**

Add TELEGRAM_REMIND_CASE to contracts and gateway allowlists. Preserve command dedup in build_external_command_record:

~~~python
record = {
    "tenant_id": tenant_id,
    "conversation_id": conversation_id,
    "chat_id": chat_id,
    "thread_id": thread_id,
    "inbound_event_id": inbound_event_id,
    "command_type": command_type,
    "payload_json": command.get("payload") or {},
    "status": "PENDING",
}
if command.get("dedup_key"):
    record["dedup_key"] = command["dedup_key"]
return record
~~~

Use a key independent of inbound event and reminder kind:

~~~python
def build_money_case_followup_dedup_key(case: dict, source_thread_id: str) -> str:
    return (
        f"telegram.case.followup:{case['telegram_chat_id']}:"
        f"{case['root_message_id']}:{source_thread_id}"
    )
~~~

- [ ] **Step 6: Merge supplements and completion dispute**

In handle_waiting_backend, emit one reminder when previous_thread_continuation and a matched eligible case exist. Put raw text, slot updates, and new attachment URLs in supplement. For a completed dispute, place telegram_case_update at the top level of the returned graph state, not inside the external-command payload:

~~~python
"follow_up_kind": "completion_dispute",
"telegram_case_update": {
    "telegram_case_id": matched_case["id"],
    "status": "completion_disputed",
},
~~~

If reminder is emitted, do not emit TELEGRAM_APPEND_TO_CASE. Same-thread behavior stays unchanged.

Use the existing language policy to return a safe localized LiveChat acknowledgement equivalent to “we are rechecking and will update this thread.” Add a test that the acknowledgement does not expose Telegram, case IDs, or claim that staff has already replied. When _resolved_state confirms customer receipt, put a top-level telegram_case_update with status completed_confirmed_by_customer.

- [ ] **Step 7: Run focused tests**

~~~bash
uv run --group dev pytest tests/unit/test_telegram_case_followup.py tests/unit/graph/test_nodes.py tests/unit/test_gateway.py tests/unit/workflows/test_waiting_backend_classifier.py -q
~~~

Expected: PASS.

- [ ] **Step 8: Commit only task-owned hunks**

~~~bash
git add src/app/services/telegram_case_followup.py tests/unit/test_telegram_case_followup.py
git add -p -- src/app/graph/state.py src/app/graph/nodes.py src/app/services/gateway.py src/app/services/outbox.py src/app/workflows/command_contracts.py src/app/workflows/sop_command_builder.py src/app/workflows/waiting_backend_classifier.py tests/unit/graph/test_nodes.py tests/unit/test_gateway.py tests/unit/workflows/test_waiting_backend_classifier.py
git diff --cached --check
git diff --cached
git commit -m "feat: route cross-thread money case follow-ups"
~~~

---

### Task 4: Render Safe English Follow-Up Messages

**Files:**
- Modify: src/app/services/telegram_case_followup.py
- Modify: src/app/services/telegram_case_card.py
- Modify: src/app/channels/telegram/sender_client.py
- Test: tests/unit/test_telegram_case_followup.py
- Test: tests/unit/test_telegram_case_card.py
- Test: tests/unit/test_telegram_sender_client.py

**Interfaces:**
- Produces: summarize_customer_update(source_text: str, intent: str, translator=None) -> str
- Produces: build_telegram_case_followup(command: dict, case: dict, followup: dict, customer_update_en: str) -> dict
- Produces: TelegramSenderClient.send_case_followup(followup: dict) -> dict
- Consumes translator protocol: translate_followup(source_text: str, intent: str) -> str

- [ ] **Step 1: Write validation and sender tests**

~~~python
def test_non_english_summary_uses_fixed_deposit_fallback():
    translator = FakeTranslator("存款还没有到账")
    assert summarize_customer_update("存款还没有到账", "deposit_missing", translator) == (
        "The customer reports that the deposit has still not been credited."
    )


def test_summary_preserves_order_amount_and_duration():
    translator = FakeTranslator("Order TX123 for 50 has not arrived after two days.")
    result = summarize_customer_update("订单 TX123 金额 50 两天了还没到", "deposit_missing", translator)
    assert "TX123" in result and "50" in result and "two days" in result


def test_sender_replies_to_root_without_editing(monkeypatch):
    result = client.send_case_followup(followup_payload(root=123, attachment="https://cdn/new.png"))
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["reply_to_message_id"] == 123
    assert all(method != "editMessageText" for method, _body in calls)
    assert calls[1][1]["reply_to_message_id"] == result["message_id"]
~~~

- [ ] **Step 2: Run and confirm red**

~~~bash
uv run --group dev pytest tests/unit/test_telegram_case_followup.py tests/unit/test_telegram_case_card.py tests/unit/test_telegram_sender_client.py -q
~~~

Expected: FAIL because rendering/sending APIs do not exist.

- [ ] **Step 3: Implement validated summaries**

Require English letters, no CJK, length <= 300, and preservation of extracted order IDs, numeric values, and duration facts. Normalize localized durations such as 两天, 2 días, and two days to one canonical quantity/unit before comparison. Reject status claims absent from source:

~~~python
def summarize_customer_update(source_text: str, intent: str, translator=None) -> str:
    fallback = CUSTOMER_UPDATE_FALLBACK[intent]
    if translator is None:
        return fallback
    try:
        candidate = str(translator.translate_followup(source_text, intent) or "").strip()
    except Exception:
        return fallback
    return candidate if validate_customer_update(source_text, candidate)["ok"] else fallback
~~~

- [ ] **Step 4: Implement exact English mappings**

~~~python
CASE_TYPE = {
    "deposit_missing": "Deposit Not Credited",
    "withdrawal_missing": "Withdrawal Not Received",
}
ACTION_REQUIRED = {
    "deposit_missing": "Please recheck whether the deposit has been credited and reply with the latest status.",
    "withdrawal_missing": "Please recheck whether the withdrawal has been completed and reply with the latest status.",
}
~~~

Render the approved FOLLOW-UP REQUIRED, CREDITING RESULT DISPUTED, and COMPLETION DISPUTED templates. Do not interpolate raw non-English text.

- [ ] **Step 5: Implement new-message delivery**

~~~python
def send_case_followup(self, followup: dict[str, Any]) -> dict[str, Any]:
    main = self.send_message(
        followup["chat_id"], followup["text"],
        message_thread_id=followup.get("thread_id"),
        reply_to_message_id=int(followup["root_message_id"]),
    )
    message_id = int(main["result"]["message_id"])
    attachments = [
        self.send_photo_from_url(
            followup["chat_id"], item["url"], caption=item.get("name"),
            message_thread_id=followup.get("thread_id"), reply_to_message_id=message_id,
        )
        for item in followup.get("attachments") or []
    ]
    return {"ok": True, "message_id": message_id, "attachment_results": attachments}
~~~

- [ ] **Step 6: Run focused tests and commit**

~~~bash
uv run --group dev pytest tests/unit/test_telegram_case_followup.py tests/unit/test_telegram_case_card.py tests/unit/test_telegram_sender_client.py -q
git add -p -- src/app/services/telegram_case_followup.py src/app/services/telegram_case_card.py src/app/channels/telegram/sender_client.py tests/unit/test_telegram_case_followup.py tests/unit/test_telegram_case_card.py tests/unit/test_telegram_sender_client.py
git diff --cached --check
git diff --cached
git commit -m "feat: render English Telegram follow-up reminders"
~~~

Expected: PASS before commit.

---

### Task 5: Execute Reminders and Route Staff Replies to the Latest Thread

**Files:**
- Modify: src/app/workers/external_command_worker.py
- Modify: src/app/db/telegram_repositories.py
- Modify: src/app/db/repositories.py
- Modify: src/app/workers/telegram_reply_consumer.py
- Modify: src/app/workers/service_runner.py
- Test: tests/unit/test_external_command_worker.py
- Test: tests/unit/test_telegram_case_repository.py
- Test: tests/unit/test_repositories.py
- Test: tests/unit/test_telegram_reply_consumer.py
- Test: tests/unit/test_service_runner.py

**Interfaces:**
- Consumes Task 1 repository, Task 2 classifier, and Task 4 renderer/sender.
- Produces processed audit result telegram.case.reminded.
- Produces telegram_case_messages kinds follow_up and follow_up_attachment.
- Produces transaction support for graph_state["telegram_case_update"].
- Produces: effective_follow_up_kind(case_status: str, raw_user_input: str) -> str | None; returns pending_follow_up, completion_dispute, or None when a fresh status recheck cancels the send.
- Produces: finish_without_another_send(command: dict, reservation: dict) -> dict; marks duplicate work terminal without a Telegram API call.
- Produces: cancel_stale_followup(command: dict, reservation: dict, case_status: str) -> dict; marks the reserved follow-up canceled and emits no Telegram message.

- [ ] **Step 1: Write worker and current-route tests**

~~~python
def test_real_reminder_reserves_summarizes_sends_and_records():
    result = asyncio.run(process_pending_commands(
        command_repo(remind_command()), result_repository=result_repo,
        telegram_case_repository=case_repo, dry_run=False, emit_result=True,
        execute_telegram=True, settings=telegram_settings(),
        telegram_client_factory=lambda _settings: sender,
        telegram_followup_translator=FakeTranslator("The withdrawal is still not received."),
    ))
    assert case_repo.reserved[0]["source_thread_id"] == "thread-new"
    assert sender.followups[0]["root_message_id"] == 123
    assert case_repo.sent[0]["telegram_message_id"] == 456
    assert result_repo.inserted[0]["result_type"] == "telegram.case.reminded"


def test_staff_reply_to_root_uses_current_thread_route():
    case = asyncio.run(repository.find_by_reply_message("-1001", 123))
    assert case["conversation_id"] == "livechat:chat-1:thread-new"
    assert case["thread_id"] == "thread-new"
~~~

- [ ] **Step 2: Run and confirm red**

~~~bash
uv run --group dev pytest tests/unit/test_external_command_worker.py tests/unit/test_telegram_case_repository.py tests/unit/test_repositories.py tests/unit/test_telegram_reply_consumer.py tests/unit/test_service_runner.py -q
~~~

Expected: FAIL because reminder execution and transactional case updates do not exist.

- [ ] **Step 3: Add reminder worker branch**

Add telegram.remind_case to supported types, Telegram semaphore routing, dry-run mapping, and real execution dependencies. Execute:

~~~python
reservation = await telegram_case_repository.reserve_followup(
    external_command_id=command["id"],
    telegram_case_id=int(payload["telegram_case_id"]),
    source_conversation_id=command["conversation_id"],
    source_thread_id=command["thread_id"],
    follow_up_kind=payload["follow_up_kind"],
    previous_status=payload["previous_status"],
)
case = reservation["case"]
if reservation["duplicate"] and reservation["status"] in {"sent", "delivery_uncertain"}:
    return await finish_without_another_send(command, reservation)
kind = effective_follow_up_kind(case["status"], payload["raw_user_input"])
if kind is None:
    return await cancel_stale_followup(command, reservation, case["status"])
customer_update = summarize_customer_update(
    payload["raw_user_input"], case["intent"], telegram_followup_translator,
)
followup = build_telegram_case_followup(
    command, case, {**reservation, "follow_up_kind": kind}, customer_update,
)
delivery = client.send_case_followup(followup)
await telegram_case_repository.record_followup_sent(
    reservation["id"], delivery["message_id"],
    attachment_message_ids(delivery), customer_update,
)
~~~

Extend the existing Gemini translator adapter without changing its normal `translate()` path:

~~~python
def translate_followup(self, source_text: str, intent: str) -> str:
    prompt = (
        "Translate only the customer's latest update into one concise English sentence "
        "of at most 300 characters. Preserve transaction IDs, amounts, and durations exactly. "
        "Do not infer payment status or add facts. Return the sentence only.\n"
        f"Case intent: {intent}\nCustomer update: {source_text}"
    )
    return self._invoke_text(prompt)
~~~

Insert `telegram.case.reminded` with `status="PROCESSED"` as an audit-only result so the external result consumer does not send a second customer message:

~~~python
await result_repository.insert_result(
    _build_result_record(
        command,
        result_type="telegram.case.reminded",
        result_payload={"followup_id": reservation["id"], "telegram_message_id": delivery["message_id"]},
        status="PROCESSED",
    )
)
~~~

- [ ] **Step 4: Apply conservative delivery uncertainty**

Persist reserved -> sending -> sent. Retry only errors that prove no send, including HTTP 429. A timeout or connection loss after dispatch becomes delivery_uncertain and terminal; a second worker pass must not call sendMessage again.

- [ ] **Step 5: Record message mappings**

record_followup_sent inserts the main reminder with message_kind=follow_up and attachments with message_kind=follow_up_attachment in telegram_case_messages.

- [ ] **Step 6: Route staff replies to current conversation**

Select current route aliases:

~~~sql
COALESCE(c.current_conversation_id, c.conversation_id) AS conversation_id,
COALESCE(c.current_thread_id, c.thread_id) AS thread_id
~~~

Join conversation state using the same current conversation expression so human-active and language checks use the current thread.

- [ ] **Step 7: Apply case status in the existing DB transactions**

Both GatewayTransactionRepository and ExternalResultTransactionRepository consume and remove internal telegram_case_update before writing conversation state:

~~~python
case_update = graph_state.pop("telegram_case_update", None)
if case_update:
    await self.telegram_case_repository.update_case_status_on_connection(
        conn, int(case_update["telegram_case_id"]), str(case_update["status"])
    )
~~~

This covers staff completion, customer confirmation, and completion dispute atomically.

- [ ] **Step 8: Run focused tests and commit**

~~~bash
uv run --group dev pytest tests/unit/test_external_command_worker.py tests/unit/test_telegram_case_repository.py tests/unit/test_repositories.py tests/unit/test_telegram_reply_consumer.py tests/unit/test_service_runner.py -q
git add -p -- src/app/workers/external_command_worker.py src/app/db/telegram_repositories.py src/app/db/repositories.py src/app/workers/telegram_reply_consumer.py src/app/workers/service_runner.py tests/unit/test_external_command_worker.py tests/unit/test_telegram_case_repository.py tests/unit/test_repositories.py tests/unit/test_telegram_reply_consumer.py tests/unit/test_service_runner.py
git diff --cached --check
git diff --cached
git commit -m "feat: deliver Telegram follow-ups to current threads"
~~~

Expected: PASS before commit.

---

### Task 6: Verify the Closed Loop and Regressions

**Files:**
- Modify: docs/p9-a-telegram-sop-closed-loop.md
- Test: all files modified in Tasks 1-5.

**Interfaces:**
- Consumes the complete command/result/reply loop.
- Produces a documented smoke sequence and passing regression suite.

- [ ] **Step 1: Document the deterministic smoke**

Add this sequence:

~~~text
1. Create a deposit_missing or withdrawal_missing card and record its root Telegram message.
2. Reply from Telegram with an explicit under-review message.
3. Open a new LiveChat thread and ask why the same transaction is still not received.
4. Verify exactly one English FOLLOW-UP REQUIRED reply under the original root card.
5. Send another status question in the same new thread and verify no second Telegram reminder.
6. Reply to the original root card and verify the customer receives the result in the new thread.
7. Repeat with a completed staff result followed by a still-not-received dispute and verify the dispute template.
~~~

- [ ] **Step 2: Run the narrow complete feature suite**

~~~bash
uv run --group dev pytest \
  tests/unit/test_bootstrap.py \
  tests/unit/test_telegram_case_repository.py \
  tests/unit/test_telegram_case_status.py \
  tests/unit/test_telegram_case_followup.py \
  tests/unit/test_telegram_case_card.py \
  tests/unit/test_telegram_sender_client.py \
  tests/unit/test_external_command_worker.py \
  tests/unit/test_telegram_reply_consumer.py \
  tests/unit/test_gateway.py \
  tests/unit/test_repositories.py \
  tests/unit/test_service_runner.py \
  tests/unit/graph/test_nodes.py \
  tests/unit/workflows/test_waiting_backend_classifier.py -q
~~~

Expected: PASS with no failed feature tests.

- [ ] **Step 3: Run the full unit suite**

~~~bash
uv run --group dev pytest tests/unit -q
~~~

Expected: PASS. Reproduce any claimed pre-existing failure without the feature patch and report its exact test name.

- [ ] **Step 4: Inspect safety invariants**

~~~bash
rg -n "telegram.remind_case|FOLLOW-UP REQUIRED|COMPLETION DISPUTED|CREDITING RESULT DISPUTED" src tests docs
rg -n "editMessageText" src/app/channels/telegram src/app/workers
git diff --check
~~~

Expected: reminder code has no editMessageText path; only existing append code edits; diff check is empty.

- [ ] **Step 5: Review the scoped diff**

Confirm:

~~~text
- both money intents
- exact/inherited/single match and ambiguous clarification
- one case/thread unique key independent of kind
- merged supplement and attachment
- deterministic status and completion dispute
- fixed English fallback and fact validation
- reply-to-root sendMessage and current-thread staff reply routing
- no unrelated refactor or pre-existing dirty hunk staged
~~~

- [ ] **Step 6: Commit documentation**

~~~bash
git add docs/p9-a-telegram-sop-closed-loop.md
git commit -m "docs: verify Telegram cross-thread follow-ups"
~~~

---

## Final Verification Gate

Before claiming complete, invoke superpowers:verification-before-completion and rerun the narrow suite plus the full unit suite. Report exact outputs, pre-existing failures, final scoped files, and whether commits were possible without including unrelated dirty-worktree changes.
