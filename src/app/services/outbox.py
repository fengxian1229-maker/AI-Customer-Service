FIXED_REPLY_TEXT = "Hello, I received your message. How can I help you today?"


def build_text_outbox(chat_id: str | None, thread_id: str | None, conversation_id: str, inbound_event_id: int | None = None) -> dict:
    return {
        "chat_id": chat_id,
        "thread_id": thread_id,
        "action_type": "send_event",
        "message_type": "text",
        "payload_json": {
            "type": "message",
            "text": FIXED_REPLY_TEXT,
        },
        "status": "PENDING",
        "conversation_id": conversation_id,
        "inbound_event_id": inbound_event_id,
    }
