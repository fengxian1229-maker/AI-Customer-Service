from typing import Any

from app.workflows.slot_extractors import attachment_urls, extract_amount, extract_channel, extract_identity, extract_order_id


PROTECTED_SLOT_KEYS = {
    "telegram_case_id",
    "telegram_message_id",
    "telegram_target_chat_id",
    "telegram_message_thread_id",
}


def extract_sop_slots(
    intent: str,
    current_slot_memory: dict[str, Any] | None,
    latest_user_text: str | None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    slot_memory = dict(current_slot_memory or {})
    extracted_slots: dict[str, Any] = {
        "account_or_phone": None,
        "amount": None,
        "payment_channel": None,
        "order_id": None,
        "deposit_screenshot": None,
        "withdrawal_screenshot": None,
    }
    text = str(latest_user_text or "")
    identity = extract_identity(text)
    if identity:
        extracted_slots["account_or_phone"] = identity["value"]
    extracted_slots["amount"] = extract_amount(text)
    extracted_slots["payment_channel"] = extract_channel(text)
    extracted_slots["order_id"] = extract_order_id(text)

    urls = attachment_urls(attachments or [])
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    if urls and intent in {"deposit_missing", "withdrawal_missing"}:
        extracted_slots[screenshot_key] = urls[0]

    for key, value in extracted_slots.items():
        if value and key not in PROTECTED_SLOT_KEYS:
            slot_memory[key] = value
    if urls:
        forwarded = list(dict.fromkeys([*slot_memory.get("forwarded_attachment_urls", []), *urls]))
        slot_memory["forwarded_attachment_urls"] = forwarded
    missing = []
    if not slot_memory.get("account_or_phone"):
        missing.append("account_or_phone")
    if intent == "deposit_missing" and not slot_memory.get("deposit_screenshot"):
        missing.append("deposit_screenshot")
    if intent == "withdrawal_missing" and not slot_memory.get("withdrawal_screenshot"):
        missing.append("withdrawal_screenshot")
    return {
        "intent": intent,
        "extracted_slots": extracted_slots,
        "attachment_classification": {
            "deposit_screenshot": extracted_slots.get("deposit_screenshot"),
            "withdrawal_screenshot": extracted_slots.get("withdrawal_screenshot"),
            "unknown_attachments": [] if extracted_slots.get(screenshot_key) else urls,
        },
        "slot_memory": slot_memory,
        "missing_slots": missing,
        "confidence": {key: 0.8 if value else 0.0 for key, value in extracted_slots.items()},
        "reason": "deterministic fallback extractor; LLM extractor can replace this contract later",
    }
