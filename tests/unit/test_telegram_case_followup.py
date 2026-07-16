from app.services.telegram_case_followup import (
    build_telegram_case_followup,
    build_money_case_followup_dedup_key,
    resolve_money_case_followup,
    summarize_customer_update,
)


def case(case_id: int, *, root: int, order_id: str | None = None, status: str = "under_review") -> dict:
    slot_memory = {"order_id": order_id} if order_id else {}
    return {
        "id": case_id,
        "intent": "withdrawal_missing",
        "status": status,
        "telegram_chat_id": "-1001",
        "root_message_id": root,
        "slot_memory": slot_memory,
    }


def test_inherited_root_matches_when_no_exact_transaction_is_named():
    result = resolve_money_case_followup(
        [case(1, root=100), case(2, root=200)],
        text="why is it still not received",
        inherited_root_message_id=200,
    )

    assert result["status"] == "matched"
    assert result["case"]["id"] == 2


def test_exact_transaction_id_wins_over_inherited_root():
    result = resolve_money_case_followup(
        [case(1, root=100, order_id="TX100"), case(2, root=200, order_id="TX200")],
        text="TX100 is still not received",
        inherited_root_message_id=200,
    )

    assert result["case"]["id"] == 1


def test_transaction_id_match_does_not_use_substrings():
    result = resolve_money_case_followup(
        [case(1, root=100, order_id="TX100"), case(2, root=200, order_id="TX200")],
        text="TX1000 is still not received",
        inherited_root_message_id=None,
    )

    assert result == {"status": "ambiguous"}


def test_multiple_cases_without_exact_match_are_ambiguous():
    result = resolve_money_case_followup(
        [case(1, root=100), case(2, root=200)],
        text="still not received",
        inherited_root_message_id=None,
    )

    assert result == {"status": "ambiguous"}


def test_exact_order_id_wins_over_other_candidates():
    result = resolve_money_case_followup(
        [case(1, root=100, order_id="TX100"), case(2, root=200, order_id="TX200")],
        text="TX200 is still not received",
        inherited_root_message_id=None,
    )

    assert result["status"] == "matched"
    assert result["case"]["id"] == 2


def test_single_open_case_matches_followup_without_order_id():
    result = resolve_money_case_followup(
        [case(1, root=100)],
        text="怎么还没有收到",
        inherited_root_message_id=None,
    )

    assert result["status"] == "matched"


def test_completed_case_requires_explicit_still_not_received_dispute():
    completed = case(1, root=100, status="completed_by_staff")

    assert resolve_money_case_followup([completed], "thanks", None) == {"status": "none"}
    result = resolve_money_case_followup([completed], "I still have not received it", None)
    assert result["status"] == "matched"
    assert result["follow_up_kind"] == "completion_dispute"


def test_waiting_customer_case_does_not_trigger_status_reminder():
    assert resolve_money_case_followup(
        [case(1, root=100, status="waiting_customer")], "still not received", None
    ) == {"status": "none"}


def test_customer_confirmation_matches_case_without_creating_followup_kind():
    result = resolve_money_case_followup(
        [case(1, root=100, order_id="TX100", status="completed_by_staff")],
        "TX100 has arrived now",
        None,
    )

    assert result["status"] == "matched"
    assert result["follow_up_kind"] == "customer_confirmed_resolved"


def test_dedup_key_is_independent_of_reminder_kind_and_event():
    value = build_money_case_followup_dedup_key(case(1, root=100), "thread-new")
    assert value == "telegram.case.followup:-1001:100:thread-new"


class FakeTranslator:
    def __init__(self, result: str):
        self.result = result

    def translate_followup(self, source_text: str, intent: str) -> str:
        return self.result


def test_non_english_summary_uses_fixed_deposit_fallback():
    result = summarize_customer_update(
        "存款还没有到账", "deposit_missing", FakeTranslator("存款还没有到账")
    )
    assert result == "The customer reports that the deposit has still not been credited."


def test_spanish_summary_uses_fixed_english_fallback():
    result = summarize_customer_update(
        "El depósito todavía no llegó",
        "deposit_missing",
        FakeTranslator("El cliente informa que el deposito todavía no llegó."),
    )
    assert result == "The customer reports that the deposit has still not been credited."


def test_summary_inventing_review_status_or_eta_uses_fallback():
    result = summarize_customer_update(
        "Withdrawal TX123 is still not received",
        "withdrawal_missing",
        FakeTranslator("Withdrawal TX123 is under review and will arrive within 24 hours."),
    )
    assert result == "The customer reports that the withdrawal has still not been received."


def test_summary_cannot_drop_source_negation():
    result = summarize_customer_update(
        "The withdrawal has not been received",
        "withdrawal_missing",
        FakeTranslator("The withdrawal has been received."),
    )
    assert result == "The customer reports that the withdrawal has still not been received."


def test_summary_preserves_order_amount_and_duration():
    result = summarize_customer_update(
        "订单 TX123 金额 50 两天了还没到",
        "deposit_missing",
        FakeTranslator("Order TX123 for 50 has not arrived after two days."),
    )
    assert "TX123" in result and "50" in result and "two days" in result


def test_summary_missing_critical_fact_uses_fallback():
    result = summarize_customer_update(
        "Order TX123 amount 50 is still not received",
        "withdrawal_missing",
        FakeTranslator("The withdrawal is still not received."),
    )
    assert result == "The customer reports that the withdrawal has still not been received."


def test_normal_followup_renderer_is_pure_english_and_deterministic():
    rendered = build_telegram_case_followup(
        {
            "conversation_id": "livechat:chat-1:thread-new",
            "thread_id": "thread-new",
            "payload_json": {"supplement": {"attachment_urls": ["https://cdn/new.png"]}},
        },
        {
            "intent": "withdrawal_missing",
            "telegram_chat_id": "-1001",
            "telegram_message_thread_id": 12,
            "root_message_id": 200,
        },
        {"follow_up_number": 2, "follow_up_kind": "pending_follow_up", "previous_status": "under_review"},
        "The customer reports that withdrawal TX123 is still not received.",
    )

    assert rendered["text"].startswith("🔔 FOLLOW-UP REQUIRED")
    assert "Case Type: Withdrawal Not Received" in rendered["text"]
    assert "Follow-up: #2" in rendered["text"]
    assert "Previous Status: Under Review" in rendered["text"]
    assert "客户" not in rendered["text"]
    assert rendered["root_message_id"] == 200
    assert rendered["attachments"] == [{"url": "https://cdn/new.png", "name": "supplement"}]


def test_completion_dispute_renderer_uses_case_specific_template():
    rendered = build_telegram_case_followup(
        {"thread_id": "thread-new", "payload_json": {}},
        {
            "intent": "deposit_missing",
            "telegram_chat_id": "-1001",
            "telegram_message_thread_id": None,
            "root_message_id": 201,
        },
        {"follow_up_number": 3, "follow_up_kind": "completion_dispute", "previous_status": "completed_by_staff"},
        "ignored",
    )

    assert rendered["text"].startswith("⚠️ CREDITING RESULT DISPUTED")
    assert "completion evidence" in rendered["text"]
