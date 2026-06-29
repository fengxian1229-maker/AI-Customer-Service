from app.workflows.sop_slot_extractor import extract_sop_slots


def test_deterministic_sop_slot_extractor_does_not_overwrite_protected_telegram_fields():
    result = extract_sop_slots(
        "deposit_missing",
        {"telegram_case_id": "tg:123", "telegram_message_id": 123},
        "mi usuario es andy123",
        [{"url": "https://cdn.example/deposit.png"}],
    )

    assert result["slot_memory"]["telegram_case_id"] == "tg:123"
    assert result["slot_memory"]["telegram_message_id"] == 123
    assert result["slot_memory"]["account_or_phone"] == "andy123"
    assert result["slot_memory"]["deposit_screenshot"] == "https://cdn.example/deposit.png"
