FIXED_REPLY_TEXT = "Hello, I received your message. How can I help you today?"


def build_text_outbox(
    chat_id: str | None,
    thread_id: str | None,
    conversation_id: str,
    inbound_event_id: int | None = None,
    text: str = FIXED_REPLY_TEXT,
) -> dict:
    return {
        "chat_id": chat_id,
        "thread_id": thread_id,
        "action_type": "send_event",
        "message_type": "text",
        "payload_json": {
            "type": "message",
            "text": text,
        },
        "status": "PENDING",
        "conversation_id": conversation_id,
        "inbound_event_id": inbound_event_id,
    }


def build_command_outbox(
    chat_id: str | None,
    thread_id: str | None,
    conversation_id: str,
    inbound_event_id: int,
    command: dict,
) -> dict:
    command_type = str(command["type"])
    if command_type == "livechat.send_text":
        return build_text_outbox(
            chat_id=chat_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            inbound_event_id=inbound_event_id,
            text=str((command.get("payload") or {}).get("text") or ""),
        )
    return {
        "chat_id": chat_id,
        "thread_id": thread_id,
        "action_type": command_type,
        "message_type": "external_command",
        "payload_json": {
            "type": command_type,
            "payload": command.get("payload") or {},
        },
        "status": "PENDING_EXTERNAL",
        "conversation_id": conversation_id,
        "inbound_event_id": inbound_event_id,
    }
