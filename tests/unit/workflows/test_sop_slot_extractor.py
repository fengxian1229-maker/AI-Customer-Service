from app.workflows.sop_slot_extractor import extract_sop_slots


def test_deterministic_sop_slot_extractor_does_not_overwrite_protected_telegram_fields():
    result = extract_sop_slots(
        "deposit_missing",
        {"telegram_case_id": "tg:123", "telegram_message_id": 123},
        "mi usuario es andy123",
        [
            {
                "url": "https://cdn.example/deposit.png",
                "verified_receipt_attachment": True,
                "receipt_kind": "deposit",
            }
        ],
    )

    assert result["slot_memory"]["telegram_case_id"] == "tg:123"
    assert result["slot_memory"]["telegram_message_id"] == 123
    assert result["slot_memory"]["account_or_phone"] == "andy123"
    assert result["slot_memory"]["deposit_screenshot"] == "https://cdn.example/deposit.png"


def test_deterministic_sop_slot_extractor_treats_standalone_digits_as_phone():
    result = extract_sop_slots(
        "deposit_missing",
        {},
        "13800138000",
        [],
    )

    assert result["slot_memory"]["phone"] == "13800138000"
    assert result["slot_memory"]["account_or_phone"] == "13800138000"
    assert result["extracted_slots"]["phone"] == "13800138000"


def test_deterministic_sop_slot_extractor_keeps_labelled_amount_out_of_phone():
    result = extract_sop_slots(
        "deposit_missing",
        {},
        "金额 1000",
        [],
    )

    assert result["slot_memory"]["amount"] == "1000"
    assert "phone" not in result["slot_memory"]
    assert "account_or_phone" not in result["slot_memory"]


def test_deterministic_sop_slot_extractor_does_not_collect_order_id_for_image_sops():
    result = extract_sop_slots(
        "withdrawal_missing",
        {},
        "提款订单 W987654 没到账",
        [],
    )

    assert "order_id" not in result["slot_memory"]
    assert "withdrawal_order_id" not in result["slot_memory"]
    assert "order_id" not in result["extracted_slots"]


def test_deterministic_sop_slot_extractor_accepts_unverified_image_for_active_money_sop():
    result = extract_sop_slots(
        "deposit_missing",
        {"phone": "13800138000"},
        "",
        [{"url": "https://cdn.example/upload.png", "content_type": "image/png"}],
    )

    assert result["slot_memory"]["deposit_screenshot"] == "https://cdn.example/upload.png"
    assert result["missing_slots"] == []


def test_deterministic_sop_slot_extractor_rejects_explicit_opposite_kind_image():
    result = extract_sop_slots(
        "withdrawal_missing",
        {"phone": "13800138000"},
        "",
        [
            {
                "url": "https://cdn.example/deposit.png",
                "content_type": "image/png",
                "receipt_kind": "deposit",
            }
        ],
    )

    assert "withdrawal_screenshot" not in result["slot_memory"]
    assert result["missing_slots"] == ["withdrawal_screenshot"]
