from app.services.telegram_case_card import build_telegram_case_append, build_telegram_case_card


def test_case_card_includes_username_and_phone_separately():
    card = build_telegram_case_card(
        {
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "payload_json": {
                "intent": "withdrawal_missing",
                "active_workflow": "withdrawal_missing",
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
    assert "[Customer update]" in append["text"]
    assert "交易号 TX123456" in append["text"]
    assert "customer_sent_supplement" in append["text"]


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
    assert "https://cdn.example/original.png" not in str(append)
