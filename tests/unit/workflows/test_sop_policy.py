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


def test_waiting_backend_unverified_image_attachment_is_not_supplement():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"telegram_case_id": "tg:123", "telegram_message_id": 123},
        workflow_stage="waiting_backend",
        latest_text="",
        attachments=[{"url": "https://cdn.example/landscape.png", "mime_type": "image/png"}],
    )

    assert result["action"] == "waiting_followup"
    assert result["reason"] == "customer_asked_status_or_unclear"


def test_waiting_backend_verified_matching_receipt_attachment_appends():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"telegram_case_id": "tg:123", "telegram_message_id": 123},
        workflow_stage="waiting_backend",
        latest_text="",
        attachments=[
            {
                "url": "https://cdn.example/deposit.png",
                "mime_type": "image/png",
                "verified_receipt_attachment": True,
                "receipt_kind": "deposit",
            }
        ],
    )

    assert result["action"] == "append_to_case"


def test_waiting_backend_supplement_and_human_with_telegram_case_appends_first():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"telegram_case_id": "tg:123", "telegram_message_id": 123},
        workflow_stage="waiting_backend",
        latest_text="交易号 TX123456, quiero humano",
        attachments=[],
    )

    assert result["action"] == "append_to_case"
    assert result["reason"] == "customer_sent_supplement"


def test_waiting_backend_supplement_and_human_without_telegram_case_handoffs():
    result = evaluate_sop_policy(
        "deposit_missing",
        {},
        workflow_stage="waiting_backend",
        latest_text="交易号 TX123456, quiero humano",
        attachments=[],
    )

    assert result["action"] == "human_handoff"
    assert result["reason"] == "customer_requested_human_after_supplement"


def test_waiting_backend_human_only_handoffs():
    result = evaluate_sop_policy(
        "deposit_missing",
        {},
        workflow_stage="waiting_backend",
        latest_text="quiero hablar con un agente humano",
        attachments=[],
    )

    assert result["action"] == "human_handoff"
    assert result["reason"] == "customer_requested_human"


def test_human_active_blocks_sop_side_effects():
    result = evaluate_sop_policy(
        "deposit_missing",
        {"account_or_phone": "andy123", "deposit_screenshot": "https://cdn.example/a.png"},
        conversation_status="HUMAN_ACTIVE",
        workflow_stage="collecting_slots",
    )

    assert result["action"] == "blocked"
    assert result["allowed"] is False
