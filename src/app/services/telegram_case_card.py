from datetime import datetime, timezone
from typing import Any


def build_telegram_case_card(command: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    payload = command.get("payload_json") or command.get("payload") or {}
    slot_memory = dict(payload.get("slot_memory") or {})
    intent = payload.get("intent") or payload.get("active_workflow") or "customer_case"
    platform = payload.get("platform") or slot_memory.get("platform")
    attachments = _case_attachments(intent, slot_memory)
    card_text = "\n".join(
        [
            _title(intent),
            f"Case type: {intent}",
            f"Platform: {_value(platform)}",
            f"Conversation ID: {_value(payload.get('conversation_id') or command.get('conversation_id'))}",
            f"Chat ID: {_value(payload.get('chat_id') or command.get('chat_id'))}",
            f"Thread ID: {_value(payload.get('thread_id') or command.get('thread_id'))}",
            f"Active workflow: {_value(payload.get('active_workflow'))}",
            f"Username / account: {_value(slot_memory.get('account_or_phone'))}",
            f"Phone: {_value(slot_memory.get('phone'))}",
            f"Customer name: {_value(slot_memory.get('customer_name'))}",
            f"Amount: {_value(slot_memory.get('amount'))}",
            f"Payment channel: {_value(slot_memory.get('payment_channel') or slot_memory.get('channel'))}",
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
    attachment_urls = list(dict.fromkeys(str(url) for url in (supplement.get("attachment_urls") or []) if url))
    base_text = (
        payload.get("telegram_case_card_text")
        or slot_memory.get("telegram_case_card_text")
        or build_telegram_case_card(command, target)["card_text"]
    )
    base_text = _strip_hidden_url_fields(str(base_text))
    supplement_lines = [
        "[Customer supplement]",
        f"Reason: {_value(supplement.get('reason') or 'customer_supplement')}",
        f"Supplement text: {_value(_plain_text(supplement.get('text')))}",
        f"Supplemented at: {datetime.now(timezone.utc).isoformat()}",
    ]
    if supplement.get("translation_unavailable"):
        supplement_lines.append("Translation unavailable: original customer text shown.")
    text = f"{str(base_text).rstrip()}\n\n" + "\n".join(supplement_lines)
    attachments = [{"url": url, "name": "supplement", "kind": "screenshot"} for url in attachment_urls]
    return {
        "target": {"group_id": target.get("chat_id"), "topic_id": target.get("message_thread_id")},
        "chat_id": target.get("chat_id"),
        "thread_id": target.get("message_thread_id"),
        "text": text,
        "edit_message_id": reply_to_message_id,
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


def _strip_hidden_url_fields(text: str) -> str:
    hidden_prefixes = ("Screenshot:", "Attachment URLs:")
    return "\n".join(line for line in text.splitlines() if not line.startswith(hidden_prefixes))


def _plain_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(part for part in (_plain_text(item) for item in value) if part).strip()
    if isinstance(value, dict):
        if "text" in value:
            return _plain_text(value.get("text"))
        return ""
    return str(value or "").strip()


def _title(intent: str) -> str:
    if intent == "deposit_missing":
        return "[Deposit not credited]"
    if intent == "withdrawal_missing":
        return "[Withdrawal not received]"
    return "[Customer case]"


def _value(value) -> str:
    return str(value) if value not in (None, "") else "(not provided)"
