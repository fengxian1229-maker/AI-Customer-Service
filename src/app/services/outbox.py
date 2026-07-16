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
        payload = _strip_internal_payload_fields(payload)
        row = build_text_outbox(
            chat_id=chat_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            inbound_event_id=inbound_event_id,
            text=payload["text"],
        ) | {
            "action_type": "send_event",
            "command_type": command_type,
            "message_kind": "text",
            "payload_json": {"type": "message", **payload},
        }
        return _with_command_metadata(row, command)
    if command_type == "livechat.send_image":
        payload = dict(command.get("payload") or {})
        payload["asset_key"] = str(payload.get("asset_key") or "")
        payload["asset_ref"] = payload.get("asset_ref")
        payload["caption"] = str(payload.get("caption") or "")
        payload["position"] = str(payload.get("position") or "after")
        payload = _strip_internal_payload_fields(payload)
        return _with_command_metadata(
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "action_type": command_type,
                "command_type": command_type,
                "message_type": "image",
                "message_kind": "image",
                "payload_json": payload,
                "status": "PENDING",
                "conversation_id": conversation_id,
                "inbound_event_id": inbound_event_id,
            },
            command,
        )
    if command_type == "livechat.send_buttons":
        payload = dict(command.get("payload") or {})
        payload["menu_key"] = str(payload.get("menu_key") or "")
        payload = _strip_internal_payload_fields(payload)
        return _with_command_metadata({
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
        }, command)
    raise ValueError(f"Unsupported outbound command type: {command_type}")


def _strip_internal_payload_fields(payload: dict) -> dict:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"final_reply_target", "final_reply_exempt"}
    }


def _with_command_metadata(row: dict, command: dict) -> dict:
    result = dict(row)
    if command.get("dedup_key"):
        result["dedup_key"] = command["dedup_key"]
    block_index = command.get("block_index")
    if block_index is not None:
        result["block_index"] = block_index
        if not result.get("dedup_key"):
            tenant_id = result.get("tenant_id") or "default"
            conversation_id = result.get("conversation_id") or ""
            inbound_event_id = result.get("inbound_event_id") or ""
            command_type = result.get("command_type") or result.get("action_type") or str(command.get("type") or "")
            result["dedup_key"] = f"{tenant_id}:{conversation_id}:{inbound_event_id}:{command_type}:{block_index}"
    return result


def build_external_command_record(
    tenant_id: str,
    chat_id: str | None,
    thread_id: str | None,
    conversation_id: str,
    inbound_event_id: int,
    command: dict,
) -> dict:
    command_type = str(command["type"])
    record = {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "inbound_event_id": inbound_event_id,
        "command_type": command_type,
        "payload_json": command.get("payload") or {},
        "status": "PENDING",
    }
    if command.get("dedup_key"):
        record["dedup_key"] = command["dedup_key"]
    return record
