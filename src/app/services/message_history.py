from app.schemas.events import InboundEvent


def build_customer_message_from_inbound(
    event: InboundEvent,
    conversation: dict,
    inbound_event_id: int,
) -> dict:
    attachments = _extract_attachment_refs(event.payload_json or {}, event.standard_event_type)
    text_content = _extract_text(event.payload_json or {})
    message_type = "file" if event.standard_event_type == "FILE_RECEIVED" else "text"
    return {
        "conversation_id": conversation["conversation_id"],
        "tenant_id": conversation.get("tenant_id") or event.organization_id or "default",
        "channel_type": conversation.get("channel_type") or "livechat",
        "chat_id": event.chat_id,
        "thread_id": event.thread_id,
        "inbound_event_id": inbound_event_id,
        "outbound_message_id": None,
        "external_command_result_id": None,
        "sender_role": "customer",
        "message_type": message_type,
        "text_content": text_content,
        "attachment_refs": attachments,
        "source": "inbound_event",
        "occurred_at": event.occurred_at,
    }


def build_assistant_message_from_outbound(outbound_message: dict) -> dict:
    payload = outbound_message.get("payload_json") or {}
    return {
        "conversation_id": outbound_message["conversation_id"],
        "tenant_id": outbound_message.get("tenant_id") or "default",
        "channel_type": outbound_message.get("channel_type") or "livechat",
        "chat_id": outbound_message.get("chat_id"),
        "thread_id": outbound_message.get("thread_id"),
        "inbound_event_id": None,
        "outbound_message_id": outbound_message["id"],
        "external_command_result_id": None,
        "sender_role": "assistant",
        "message_type": outbound_message.get("message_type") or "text",
        "text_content": str(payload.get("text") or ""),
        "attachment_refs": [],
        "source": "sender_worker",
        "occurred_at": None,
    }


def build_external_result_summary_message(result: dict, handler: dict) -> dict:
    return {
        "conversation_id": result["conversation_id"],
        "tenant_id": result.get("tenant_id") or "default",
        "channel_type": "livechat",
        "chat_id": result.get("chat_id"),
        "thread_id": result.get("thread_id"),
        "inbound_event_id": None,
        "outbound_message_id": None,
        "external_command_result_id": result["id"],
        "sender_role": handler["summary_sender_role"],
        "message_type": "external_result",
        "text_content": handler["summary_text"],
        "attachment_refs": [],
        "source": "external_result_consumer",
        "occurred_at": None,
    }


def _extract_text(payload: dict) -> str | None:
    event = payload.get("event") or {}
    text = event.get("text") or payload.get("text") or payload.get("message")
    return str(text) if text is not None else None


def _extract_attachment_refs(payload: dict, event_type: str) -> list[dict]:
    refs = []
    for item in payload.get("attachments") or []:
        refs.append(_sanitize_attachment(item))
    event = payload.get("event") or {}
    if event_type == "FILE_RECEIVED":
        file_payload = event.get("file") if isinstance(event.get("file"), dict) else event
        refs.append(_sanitize_attachment(file_payload))
    return [ref for ref in refs if ref]


def _sanitize_attachment(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    ref = {
        "url": item.get("url") or item.get("content_url") or item.get("thumbnail_url"),
        "filename": item.get("filename") or item.get("name"),
        "mime_type": item.get("mime_type") or item.get("content_type"),
        "size": item.get("size"),
    }
    if not any(value is not None for value in ref.values()):
        return None
    return {key: value for key, value in ref.items() if value is not None}
