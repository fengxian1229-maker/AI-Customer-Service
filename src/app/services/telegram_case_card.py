from datetime import datetime, timezone
from typing import Any


def build_telegram_case_card(command: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    payload = command.get("payload_json") or command.get("payload") or {}
    slot_memory = dict(payload.get("slot_memory") or {})
    intent = payload.get("intent") or payload.get("active_workflow") or "customer_case"
    attachments = _case_attachments(intent, slot_memory)
    card_text = "\n".join(
        [
            _title(intent),
            f"Case type: {intent}",
            f"Conversation ID: {_value(payload.get('conversation_id') or command.get('conversation_id'))}",
            f"Chat ID: {_value(payload.get('chat_id') or command.get('chat_id'))}",
            f"Thread ID: {_value(payload.get('thread_id') or command.get('thread_id'))}",
            f"Active workflow: {_value(payload.get('active_workflow'))}",
            f"Username / phone: {_value(slot_memory.get('account_or_phone'))}",
            f"Amount: {_value(slot_memory.get('amount'))}",
            f"Payment channel: {_value(slot_memory.get('payment_channel') or slot_memory.get('channel'))}",
            f"Order ID: {_value(slot_memory.get('order_id') or slot_memory.get('deposit_order_id') or slot_memory.get('withdrawal_order_id'))}",
            f"Screenshot: {_value(slot_memory.get('deposit_screenshot') or slot_memory.get('withdrawal_screenshot'))}",
            f"Attachment URLs: {_value(', '.join(slot_memory.get('forwarded_attachment_urls') or []))}",
            f"Created at: {datetime.now(timezone.utc).isoformat()}",
        ]
    )
    return {
        "case_type": intent,
        "target": {"group_id": target.get("chat_id"), "topic_id": target.get("message_thread_id")},
        "chat_id": target.get("chat_id"),
        "thread_id": target.get("message_thread_id"),
        "card_text": card_text,
        "attachments": attachments,
    }


def build_telegram_case_append(command: dict[str, Any], target: dict[str, Any], reply_to_message_id: int | None = None) -> dict[str, Any]:
    payload = command.get("payload_json") or command.get("payload") or {}
    supplement = dict(payload.get("supplement") or {})
    slot_memory = dict(payload.get("slot_memory") or {})
    intent = payload.get("intent") or payload.get("active_workflow") or "customer_case"
    attachment_urls = supplement.get("attachment_urls") or []
    text = "\n".join(
        [
            "[Customer update]",
            f"Case type: {intent}",
            f"Chat ID: {_value(payload.get('chat_id') or command.get('chat_id'))}",
            f"Reason: {_value(supplement.get('reason') or 'customer_supplement')}",
            f"New text: {_value(supplement.get('text'))}",
            f"New attachments: {_value(', '.join(attachment_urls))}",
        ]
    )
    attachments = [{"url": url, "name": "supplement", "kind": "screenshot"} for url in attachment_urls]
    if not attachments:
        attachments = _case_attachments(intent, slot_memory)
    return {
        "target": {"group_id": target.get("chat_id"), "topic_id": target.get("message_thread_id")},
        "chat_id": target.get("chat_id"),
        "thread_id": target.get("message_thread_id"),
        "text": text,
        "reply_to_message_id": reply_to_message_id,
        "attachments": attachments,
    }


def _case_attachments(intent: str, slot_memory: dict[str, Any]) -> list[dict[str, str]]:
    key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    urls = []
    if slot_memory.get(key):
        urls.append(slot_memory[key])
    urls.extend(slot_memory.get("forwarded_attachment_urls") or [])
    deduped = list(dict.fromkeys(str(url) for url in urls if url))
    return [{"url": url, "name": key if index == 0 else "forwarded_attachment", "kind": "screenshot"} for index, url in enumerate(deduped)]


def _title(intent: str) -> str:
    if intent == "deposit_missing":
        return "[Deposit not credited]"
    if intent == "withdrawal_missing":
        return "[Withdrawal not received]"
    return "[Customer case]"


def _value(value) -> str:
    return str(value) if value not in (None, "") else "(not provided)"
