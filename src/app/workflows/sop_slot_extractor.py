import re
from typing import Any

from app.workflows.slot_extractors import attachment_urls, extract_amount, extract_channel, extract_identity_from_texts


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
    _drop_order_slots(slot_memory)
    if slot_memory.get("phone") and not slot_memory.get("account_or_phone"):
        slot_memory["account_or_phone"] = slot_memory["phone"]
    extracted_slots: dict[str, Any] = {
        "account_or_phone": None,
        "phone": None,
        "amount": None,
        "payment_channel": None,
        "deposit_screenshot": None,
        "withdrawal_screenshot": None,
    }
    text = str(latest_user_text or "")
    standalone_phone = _standalone_phone_digits(text)
    amount = None if standalone_phone else extract_amount(text)
    identity = None if _amount_label_present(text) else extract_identity_from_texts(text)
    if identity:
        extracted_slots["account_or_phone"] = identity["value"]
        extracted_slots["identity_kind"] = identity["type"]
        if identity.get("type") == "phone":
            extracted_slots["phone"] = identity["value"]
    if standalone_phone:
        extracted_slots["phone"] = standalone_phone
        extracted_slots["account_or_phone"] = standalone_phone
        extracted_slots["identity_kind"] = "phone"
    extracted_slots["amount"] = amount
    extracted_slots["payment_channel"] = extract_channel(text)

    urls = attachment_urls(_verified_receipt_attachments(intent, attachments or []))
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


def _verified_receipt_attachments(intent: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected_kind = "deposit" if intent == "deposit_missing" else "withdrawal" if intent == "withdrawal_missing" else None
    if expected_kind is None:
        return []
    result = []
    for attachment in attachments:
        if _is_verified_receipt_attachment(expected_kind, attachment):
            result.append(attachment)
    return result


def _is_verified_receipt_attachment(expected_kind: str, attachment: dict[str, Any]) -> bool:
    if not attachment.get("url") or not _is_image_attachment(attachment):
        return False
    receipt_kind = str(attachment.get("receipt_kind") or "").lower()
    opposite_kind = "withdrawal" if expected_kind == "deposit" else "deposit"
    if receipt_kind == opposite_kind:
        return False
    if attachment.get("verified_receipt_attachment") and receipt_kind in {"", "unknown", expected_kind}:
        return True
    analysis = attachment.get("image_analysis")
    if isinstance(analysis, dict):
        if str(analysis.get("receipt_kind") or "").lower() == opposite_kind:
            return False
        if analysis.get("is_receipt_like"):
            return True
    return str(attachment.get("content_type") or attachment.get("mime_type") or "").lower().startswith("image/")


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    content_type = str(attachment.get("content_type") or attachment.get("mime_type") or "").lower()
    if content_type.startswith("image/"):
        return True
    name = str(attachment.get("name") or attachment.get("filename") or attachment.get("url") or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"))


def _standalone_phone_digits(text: str | None) -> str | None:
    raw = str(text or "").strip()
    if re.fullmatch(r"\d{5,18}", raw):
        return raw
    return None


def _amount_label_present(text: str | None) -> bool:
    return bool(re.search(r"(?:金额|金額|monto|valor|amount)\s*[:：-]?\s*\d", str(text or ""), re.I))


def _drop_order_slots(slot_memory: dict[str, Any]) -> None:
    for key in ("order_id", "deposit_order_id", "withdrawal_order_id"):
        slot_memory.pop(key, None)
