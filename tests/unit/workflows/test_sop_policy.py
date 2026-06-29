from app.workflows.sop_policy import evaluate_sop_policy


def test_waiting_backend_supplement_without_telegram_case_waits():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"account_or_phone": "andy123"},
        workflow_stage="waiting_backend",
        latest_text="交易号 TX123456",
        attachments=[],
    )

    assert result["action"] == "waiting_followup"
    assert result["reason"] == "case_not_created_yet"


def test_waiting_backend_supplement_with_telegram_case_appends():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"telegram_case_id": "tg:123", "telegram_message_id": 123},
        workflow_stage="waiting_backend",
        latest_text="交易号 TX123456",
        attachments=[],
    )

    assert result["action"] == "append_to_case"


def test_human_active_blocks_sop_side_effects():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"account_or_phone": "andy123", "deposit_screenshot": "https://cdn.example/a.png"},
        conversation_status="HUMAN_ACTIVE",
        workflow_stage="collecting_slots",
    )

    assert result["action"] == "blocked"
    assert result["allowed"] is False
