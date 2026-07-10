from app.services.telegram_case_card import build_telegram_case_append, build_telegram_case_card


def test_case_card_includes_username_and_phone_separately():
    card = build_telegram_case_card(
        {
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "payload_json": {
                "intent": "withdrawal_missing",
                "active_workflow": "withdrawal_missing",
                "platform": "JG7",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "slot_memory": {
                    "account_or_phone": "frank",
                    "phone": "12335",
                    "withdrawal_screenshot": "https://cdn.example/withdrawal.png",
                },
            },
        },
        {"chat_id": "-100test", "message_thread_id": None},
    )

    assert "Username / account: frank" in card["card_text"]
    assert "Phone: 12335" in card["card_text"]
    assert "Platform: JG7" in card["card_text"]


def test_case_card_omits_screenshot_url_fields_but_keeps_attachments():
    card = build_telegram_case_card(
        {
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "payload_json": {
                "intent": "deposit_missing",
                "active_workflow": "deposit_missing",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "slot_memory": {
                    "deposit_screenshot": "https://cdn.example/deposit.png",
                    "forwarded_attachment_urls": ["https://cdn.example/extra.png"],
                },
            },
        },
        {"chat_id": "-100test", "message_thread_id": None},
    )

    assert "Screenshot:" not in card["card_text"]
    assert "Attachment URLs:" not in card["card_text"]
    assert "https://cdn.example/deposit.png" not in card["card_text"]
    assert "https://cdn.example/extra.png" not in card["card_text"]
    assert card["attachments"] == [
        {"url": "https://cdn.example/deposit.png", "name": "deposit_screenshot", "kind": "screenshot"},
        {"url": "https://cdn.example/extra.png", "name": "forwarded_attachment", "kind": "screenshot"},
    ]


def test_case_card_does_not_include_order_id_field():
    card = build_telegram_case_card(
        {
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "payload_json": {
                "intent": "deposit_missing",
                "active_workflow": "deposit_missing",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "slot_memory": {
                    "account_or_phone": "13800138000",
                    "phone": "13800138000",
                    "order_id": "TX123456",
                    "deposit_order_id": "D123456",
                    "deposit_screenshot": "https://cdn.example/deposit.png",
                },
            },
        },
        {"chat_id": "-100test", "message_thread_id": None},
    )

    assert "Order ID:" not in card["card_text"]
    assert "TX123456" not in card["card_text"]
    assert "D123456" not in card["card_text"]


def _append_command(supplement):
    return {
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "payload_json": {
            "intent": "deposit_missing",
            "active_workflow": "deposit_missing",
            "chat_id": "chat-1",
            "slot_memory": {
                "deposit_screenshot": "https://cdn.example/original.png",
                "forwarded_attachment_urls": ["https://cdn.example/original.png"],
            },
            "supplement": supplement,
        },
    }


def test_text_only_append_does_not_include_original_screenshot():
    append = build_telegram_case_append(
        _append_command({"text": "交易号 TX123456", "reason": "customer_sent_supplement"}),
        {"chat_id": "-100test", "message_thread_id": None},
        reply_to_message_id=123,
    )

    assert append["attachments"] == []
    assert "[Customer supplement]" in append["text"]
    assert "交易号 TX123456" in append["text"]
    assert "customer_sent_supplement" in append["text"]
    assert append["edit_message_id"] == 123


def test_append_includes_only_new_deduped_attachment_urls():
    append = build_telegram_case_append(
        _append_command(
            {
                "text": "补截图",
                "attachment_urls": ["https://cdn.example/new.png", "https://cdn.example/new.png"],
            }
        ),
        {"chat_id": "-100test", "message_thread_id": None},
        reply_to_message_id=123,
    )

    assert append["attachments"] == [{"url": "https://cdn.example/new.png", "name": "supplement", "kind": "screenshot"}]
    assert "New attachments:" not in append["text"]
    assert "https://cdn.example/new.png" not in append["text"]
    assert "https://cdn.example/original.png" not in append["text"]


def test_append_uses_existing_card_text_and_appends_english_supplement():
    command = _append_command({"text": "Transaction ID TX123456", "reason": "customer_sent_supplement"})
    command["payload_json"]["slot_memory"]["telegram_case_card_text"] = "[Deposit not credited]\nCase type: deposit_missing"

    append = build_telegram_case_append(
        command,
        {"chat_id": "-100test", "message_thread_id": None},
        reply_to_message_id=123,
    )

    assert append["text"].startswith("[Deposit not credited]\nCase type: deposit_missing")
    assert "[Customer supplement]" in append["text"]
    assert "Supplement text: Transaction ID TX123456" in append["text"]


def test_append_supplement_text_extracts_text_from_livechat_blocks():
    append = build_telegram_case_append(
        _append_command(
            {
                "text": [
                    {
                        "type": "text",
                        "text": "The user is requesting expedited handling.",
                        "extras": {"signature": "secret"},
                    }
                ],
                "reason": "customer_sent_supplement",
            }
        ),
        {"chat_id": "-100test", "message_thread_id": None},
        reply_to_message_id=123,
    )

    assert "Supplement text: The user is requesting expedited handling." in append["text"]
    assert "signature" not in append["text"]
    assert "{'type': 'text'" not in append["text"]
