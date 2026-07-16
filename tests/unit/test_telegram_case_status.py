import pytest

from app.services.telegram_case_status import classify_money_case_status, normalize_legacy_case_status


@pytest.mark.parametrize(
    "intent,text",
    [
        ("deposit_missing", "The deposit has been credited successfully."),
        ("withdrawal_missing", "The withdrawal has been completed successfully."),
    ],
)
def test_matching_completion_is_completed_by_staff(intent, text):
    assert classify_money_case_status(intent, text) == "completed_by_staff"


@pytest.mark.parametrize("text", ["still checking", "under review", "processing, please wait"])
def test_wait_language_is_under_review(text):
    assert classify_money_case_status("withdrawal_missing", text) == "under_review"


def test_customer_information_request_is_waiting_customer():
    assert classify_money_case_status(
        "deposit_missing", "Please provide the deposit receipt and order number."
    ) == "waiting_customer"


def test_unknown_text_is_not_completed():
    assert classify_money_case_status("deposit_missing", "noted") == "under_review"


def test_opposite_transaction_completion_does_not_close_case():
    assert classify_money_case_status(
        "withdrawal_missing", "Deposit credited successfully"
    ) != "completed_by_staff"


@pytest.mark.parametrize(
    "text",
    ["Withdrawal was not completed", "The withdrawal has not been received"],
)
def test_negated_completion_does_not_close_case(text):
    assert classify_money_case_status("withdrawal_missing", text) != "completed_by_staff"


def test_disputed_and_customer_confirmed_statuses_are_sticky_for_unknown_text():
    assert classify_money_case_status("deposit_missing", "noted", "completion_disputed") == "completion_disputed"
    assert classify_money_case_status(
        "deposit_missing", "noted", "completed_confirmed_by_customer"
    ) == "completed_confirmed_by_customer"


@pytest.mark.parametrize("status", ["completion_disputed", "completed_confirmed_by_customer"])
@pytest.mark.parametrize("reply", ["still checking", "please provide the receipt", "completed successfully"])
def test_disputed_and_customer_confirmed_statuses_never_regress(status, reply):
    assert classify_money_case_status("deposit_missing", reply, status) == status


def test_legacy_case_without_staff_reply_is_awaiting_review():
    assert normalize_legacy_case_status({"status": "created", "slot_memory": {}}) == "awaiting_review"


def test_legacy_wait_reply_is_under_review():
    assert normalize_legacy_case_status(
        {"status": "created", "slot_memory": {"last_telegram_staff_reply_type": "long_wait"}}
    ) == "under_review"


def test_legacy_resolved_slot_requires_matching_completion_text():
    case = {
        "status": "created",
        "intent": "withdrawal_missing",
        "slot_memory": {
            "telegram_case_resolved_at": "2026-07-15 10:00:00",
            "telegram_case_resolution_text": "Withdrawal completed successfully",
        },
    }
    assert normalize_legacy_case_status(case) == "completed_by_staff"
