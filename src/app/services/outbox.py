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
        payload = dict(command.get("payload") or {})
        payload["text"] = str(payload.get("text") or "")
        return build_text_outbox(
            chat_id=chat_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            inbound_event_id=inbound_event_id,
            text=payload["text"],
        ) | {"payload_json": {"type": "message", **payload}}
    if command_type == "livechat.send_buttons":
        payload = dict(command.get("payload") or {})
        payload["menu_key"] = str(payload.get("menu_key") or "")
        return {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "action_type": command_type,
            "command_type": command_type,
            "message_type": "buttons",
            "message_kind": "buttons",
            "payload_json": payload,
            "status": "PENDING",
            "conversation_id": conversation_id,
            "inbound_event_id": inbound_event_id,
        }
    raise ValueError(f"Unsupported outbound command type: {command_type}")


def build_external_command_record(
    tenant_id: str,
    chat_id: str | None,
    thread_id: str | None,
    conversation_id: str,
    inbound_event_id: int,
    command: dict,
) -> dict:
    command_type = str(command["type"])
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "inbound_event_id": inbound_event_id,
        "command_type": command_type,
        "payload_json": command.get("payload") or {},
        "status": "PENDING",
    }
