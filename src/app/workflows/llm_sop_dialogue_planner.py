from __future__ import annotations

from typing import Any

from app.workflows.slot_extractors import attachment_urls
from app.workflows.sop_definitions import SopDefinition, get_sop_definition
from app.workflows.sop_slot_extractor import extract_sop_slots

PROTECTED_SLOT_KEYS = {
    "telegram_case_id",
    "telegram_message_id",
    "telegram_target_chat_id",
    "telegram_message_thread_id",
}

INTENT_RELATIONS = {
    "current_sop_supplement",
    "current_workflow_resolution",
    "faq_interrupt",
    "new_issue",
    "human_request",
    "unclear",
}

LOW_CONFIDENCE_THRESHOLD = 0.55
IMAGE_COLLECTION_SOPS = {"deposit_missing", "withdrawal_missing"}
ORDER_SLOT_KEYS = {"order_id", "deposit_order_id", "withdrawal_order_id"}
IDENTITY_SLOT_KEYS = {"account_or_phone", "phone"}
IDENTITY_SOURCE_KEYS = {"identity_source", "account_or_phone_source", "phone_source"}


def build_llm_sop_dialogue_input(state: dict[str, Any], intent: str, definition: SopDefinition) -> dict[str, Any]:
    return {
        "tenant_id": state.get("tenant_id"),
        "conversation_id": state.get("conversation_id"),
        "sop_name": intent,
        "active_workflow": state.get("active_workflow") or intent,
        "workflow_stage": state.get("workflow_stage") or "collecting_slots",
        "current_slot_memory": dict(state.get("slot_memory") or {}),
        "sop_definition": definition.as_llm_schema(),
        "latest_user_text": str(state.get("rewritten_question") or state.get("raw_user_input") or ""),
        "attachments_summary": _attachments_summary(state, intent),
        "recent_messages": list(state.get("recent_messages") or []),
        "previous_thread_memory": list(state.get("previous_thread_memory") or []),
        "reply_language": state.get("reply_language") or "unknown",
    }


def plan_sop_dialogue_from_state(state: dict[str, Any], intent: str) -> dict[str, Any]:
    definition = get_sop_definition(intent)
    if definition is None:
        return _fallback_plan(state, intent, "unsupported_sop")

    dialogue_plan = _normalise_llm_plan(state.get("llm_sop_dialogue_plan"))
    if dialogue_plan and dialogue_plan.get("status") == "accepted" and not _has_low_confidence(dialogue_plan):
        return apply_llm_sop_plan(state, intent, dialogue_plan)

    slot_result = _normalise_llm_plan(state.get("llm_sop_slot_result"))
    if slot_result and slot_result.get("status") == "accepted" and not _has_low_confidence(slot_result):
        if dialogue_plan and dialogue_plan.get("fallback_reason"):
            slot_result["fallback_reason"] = dialogue_plan["fallback_reason"]
        return apply_llm_sop_plan(state, intent, slot_result)

    fallback_reason = (dialogue_plan or slot_result or {}).get("fallback_reason") or "llm_unavailable"
    return _fallback_plan(state, intent, fallback_reason)


def apply_llm_sop_plan(state: dict[str, Any], intent: str, llm_plan: dict[str, Any]) -> dict[str, Any]:
    definition = get_sop_definition(intent)
    if definition is None:
        return _fallback_plan(state, intent, "unsupported_sop")

    slot_memory = dict(state.get("slot_memory") or {})
    if intent in IMAGE_COLLECTION_SOPS:
        _drop_order_slots(slot_memory)
    slot_updates: dict[str, Any] = {}
    dropped_slots: list[str] = list(dict.fromkeys(str(key) for key in llm_plan.get("dropped_slots") or []))
    allowed_keys = set(definition.slot_keys) | _legacy_schema_keys(intent)
    allowed_attachment_urls = _allowed_attachment_urls(state, intent, slot_memory)

    for key, value in dict(llm_plan.get("slot_updates") or llm_plan.get("extracted_slots") or {}).items():
        key = str(key)
        if key in IDENTITY_SOURCE_KEYS:
            if value:
                slot_updates[key] = value
            continue
        if key in PROTECTED_SLOT_KEYS or key not in allowed_keys or (intent in IMAGE_COLLECTION_SOPS and key in ORDER_SLOT_KEYS):
            dropped_slots.append(key)
            continue
        if value in (None, ""):
            continue
        if key in IDENTITY_SLOT_KEYS and not _identity_update_has_user_text_evidence(key, value, state):
            _record_identity_hint(slot_memory, key, value)
            dropped_slots.append(key)
            continue
        if _slot_type(definition, key) == "attachment" and str(value) not in allowed_attachment_urls:
            dropped_slots.append(key)
            continue
        slot_updates[key] = value
        if key in IDENTITY_SLOT_KEYS:
            slot_updates["identity_source"] = "user_text"

    previous_slot_memory = dict(slot_memory)
    slot_memory.update(slot_updates)
    _merge_attachment_slots(intent, slot_memory, state.get("attachments") or [])
    _sync_legacy_aliases(intent, slot_memory, slot_updates=slot_updates, previous_slot_memory=previous_slot_memory)
    if intent in IMAGE_COLLECTION_SOPS:
        _drop_order_slots(slot_memory)
    missing_slots = compute_missing_slots(intent, slot_memory)

    return {
        "status": "accepted",
        "source": "llm_sop_dialogue_planner",
        "intent_relation": _intent_relation(llm_plan),
        "slot_memory": slot_memory,
        "slot_updates": slot_updates,
        "slot_confidence": dict(llm_plan.get("slot_confidence") or llm_plan.get("confidence") or {}),
        "missing_slots": missing_slots,
        "should_ask_confirmation": bool(llm_plan.get("should_ask_confirmation")),
        "reply_draft": str(llm_plan.get("reply_draft") or ""),
        "reason": str(llm_plan.get("reason") or ""),
        "fallback_reason": llm_plan.get("fallback_reason"),
        "dropped_slots": dropped_slots,
    }


def compute_missing_slots(intent: str, slot_memory: dict[str, Any]) -> list[str]:
    definition = get_sop_definition(intent)
    if definition is None:
        return []
    missing = []
    for slot in definition.slots:
        if slot.required and not _slot_present(intent, slot.key, slot_memory):
            missing.append(slot.key)
    return missing


def _fallback_plan(state: dict[str, Any], intent: str, reason: str) -> dict[str, Any]:
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    extraction = extract_sop_slots(intent, dict(state.get("slot_memory") or {}), text, state.get("attachments", []))
    slot_memory = extraction["slot_memory"]
    _merge_attachment_slots(intent, slot_memory, state.get("attachments") or [])
    _sync_legacy_aliases(intent, slot_memory)
    if intent in IMAGE_COLLECTION_SOPS:
        _drop_order_slots(slot_memory)
    return {
        "status": "fallback",
        "source": "deterministic",
        "intent_relation": "current_sop_supplement",
        "slot_memory": slot_memory,
        "slot_updates": dict(extraction.get("extracted_slots") or {}),
        "slot_confidence": dict(extraction.get("confidence") or {}),
        "missing_slots": compute_missing_slots(intent, slot_memory),
        "should_ask_confirmation": False,
        "reply_draft": "",
        "reason": reason,
        "fallback_reason": reason,
        "dropped_slots": [],
    }


def _normalise_llm_plan(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    if plan.get("status") == "fallback":
        return plan
    result = dict(plan.get("result") or {})
    normalized = {**result, **plan}
    normalized.setdefault("status", plan.get("status") or "accepted")
    if "slot_updates" not in normalized and "extracted_slots" in normalized:
        normalized["slot_updates"] = normalized.get("extracted_slots")
    if "slot_confidence" not in normalized and "confidence" in normalized:
        normalized["slot_confidence"] = normalized.get("confidence")
    return normalized


def _has_low_confidence(plan: dict[str, Any]) -> bool:
    confidence = plan.get("slot_confidence") or plan.get("confidence") or {}
    for key, value in dict(plan.get("slot_updates") or plan.get("extracted_slots") or {}).items():
        if key in IDENTITY_SOURCE_KEYS:
            continue
        if value and float(confidence.get(key) or 1.0) < LOW_CONFIDENCE_THRESHOLD:
            return True
    return False


def _legacy_schema_keys(intent: str) -> set[str]:
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    return {"account_or_phone", screenshot_key, "channel"}


def _slot_type(definition: SopDefinition, key: str) -> str:
    if key in {"deposit_screenshot", "withdrawal_screenshot"}:
        return "attachment"
    for slot in definition.slots:
        if slot.key == key:
            return slot.type
    return "text"


def _merge_attachment_slots(intent: str, slot_memory: dict[str, Any], attachments: list[dict[str, Any]]) -> None:
    verified = _verified_receipt_attachments(intent, attachments)
    urls = attachment_urls(verified)
    if not urls:
        return
    forwarded = list(dict.fromkeys([*slot_memory.get("forwarded_attachment_urls", []), *urls]))
    slot_memory["forwarded_attachment_urls"] = forwarded
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    slot_memory.setdefault("receipt_screenshot", urls[0])
    slot_memory.setdefault(screenshot_key, urls[0])


def _verified_receipt_attachments(intent: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected_kind = "deposit" if intent == "deposit_missing" else "withdrawal" if intent == "withdrawal_missing" else None
    if expected_kind is None:
        return []
    result = []
    for attachment in attachments or []:
        verified = _verified_receipt_attachment(intent, attachment)
        if verified:
            result.append(verified)
    return result


def _verified_receipt_attachment(intent: str, attachment: dict[str, Any]) -> dict[str, Any] | None:
    expected_kind = "deposit" if intent == "deposit_missing" else "withdrawal" if intent == "withdrawal_missing" else None
    if expected_kind is None or not attachment.get("url"):
        return None
    opposite_kind = "withdrawal" if expected_kind == "deposit" else "deposit"
    if str(attachment.get("receipt_kind") or "").lower() == opposite_kind:
        return None
    if attachment.get("verified_receipt_attachment") and str(attachment.get("receipt_kind") or "").lower() == expected_kind:
        return attachment

    analysis = attachment.get("image_analysis")
    if isinstance(analysis, dict):
        receipt_kind = str(analysis.get("receipt_kind") or "").lower()
        if receipt_kind == opposite_kind:
            return None
        if analysis.get("is_receipt_like") and receipt_kind not in {"", "unknown", expected_kind}:
            return None
        if analysis.get("is_receipt_like"):
            verified = dict(attachment)
            verified["verified_receipt_attachment"] = True
            verified["receipt_kind"] = expected_kind
            return verified
    if not str(attachment.get("content_type") or attachment.get("mime_type") or "").lower().startswith("image/"):
        return None
    if not _is_image_attachment(attachment):
        return None
    verified = dict(attachment)
    verified["verified_receipt_attachment"] = True
    verified["receipt_kind"] = expected_kind
    return verified


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    content_type = str(attachment.get("content_type") or attachment.get("mime_type") or "").lower()
    if content_type.startswith("image/"):
        return True
    name = str(attachment.get("name") or attachment.get("filename") or attachment.get("url") or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"))


def _sync_legacy_aliases(
    intent: str,
    slot_memory: dict[str, Any],
    *,
    slot_updates: dict[str, Any] | None = None,
    previous_slot_memory: dict[str, Any] | None = None,
) -> None:
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    updates = dict(slot_updates or {})
    previous = dict(previous_slot_memory or {})
    if slot_memory.get("phone") and (
        not slot_memory.get("account_or_phone")
        or _should_replace_account_or_phone_with_phone(slot_memory, updates, previous)
    ):
        slot_memory["account_or_phone"] = slot_memory["phone"]
        slot_memory["identity_kind"] = "phone"
        if slot_memory.get("identity_source"):
            slot_memory.setdefault("account_or_phone_source", slot_memory["identity_source"])
    if slot_memory.get("receipt_screenshot"):
        slot_memory.setdefault(screenshot_key, slot_memory["receipt_screenshot"])
    elif slot_memory.get(screenshot_key):
        slot_memory.setdefault("receipt_screenshot", slot_memory[screenshot_key])
    if slot_memory.get("payment_channel"):
        slot_memory.setdefault("channel", slot_memory["payment_channel"])


def _should_replace_account_or_phone_with_phone(
    slot_memory: dict[str, Any],
    slot_updates: dict[str, Any],
    previous_slot_memory: dict[str, Any],
) -> bool:
    if not slot_updates.get("phone") or slot_updates.get("account_or_phone"):
        return False
    current_account = str(slot_memory.get("account_or_phone") or "").strip()
    if not current_account:
        return True
    if str(slot_memory.get("identity_kind") or "").lower() == "phone":
        return True
    if current_account != str(previous_slot_memory.get("account_or_phone") or "").strip():
        return False
    return _looks_like_spurious_account_or_phone(current_account)


def _looks_like_spurious_account_or_phone(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return True
    if lowered.isdigit() or any(ch.isdigit() for ch in lowered):
        return False
    spurious_words = {
        "indica",
        "indicar",
        "pregunta",
        "solicita",
        "proporciona",
        "usuario",
        "cliente",
        "retiro",
        "deposito",
        "depósito",
        "recibida",
        "recibido",
    }
    return lowered in spurious_words


def _drop_order_slots(slot_memory: dict[str, Any]) -> None:
    for key in ORDER_SLOT_KEYS:
        slot_memory.pop(key, None)


def _slot_present(intent: str, key: str, slot_memory: dict[str, Any]) -> bool:
    if key == "phone":
        return _identity_slot_present(slot_memory)
    if key == "receipt_screenshot":
        screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
        return bool(slot_memory.get("receipt_screenshot") or slot_memory.get(screenshot_key))
    return bool(slot_memory.get(key))


def _identity_update_has_user_text_evidence(key: str, value: Any, state: dict[str, Any]) -> bool:
    if str(state.get("identity_source") or "").lower() == "confirmed_by_user":
        return True
    raw = str(state.get("raw_user_input") or "")
    if not raw:
        return False
    normalized_value = _normalize_identity_value(value)
    if not normalized_value:
        return False
    if key == "phone" or normalized_value.isdigit():
        normalized_raw = _normalize_identity_value(raw)
        return normalized_value in normalized_raw and len(normalized_value) >= 5
    return normalized_value.lower() in raw.lower()


def _record_identity_hint(slot_memory: dict[str, Any], key: str, value: Any) -> None:
    hint = str(value or "").strip()
    if not hint:
        return
    slot_memory["image_identity_hint"] = hint
    slot_memory["image_identity_hint_key"] = key
    slot_memory["image_identity_hint_source"] = "llm_or_image"


def _identity_slot_present(slot_memory: dict[str, Any]) -> bool:
    value = slot_memory.get("phone") or slot_memory.get("account_or_phone")
    if not value:
        return False
    source = str(
        slot_memory.get("identity_source")
        or slot_memory.get("phone_source")
        or slot_memory.get("account_or_phone_source")
        or ""
    ).lower()
    if source in {"user_text", "confirmed_by_user"}:
        return True
    if source in {"image", "ocr", "llm_or_image", "image_analysis"}:
        return False
    return not _looks_like_image_identity_hint(value)


def _looks_like_image_identity_hint(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    if any(marker in text for marker in ("terminado", "ending", "last ", "últimos", "ultimos", "尾号", "尾號")):
        return True
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and len(digits) <= 4 and not text.isdigit():
        return True
    return False


def _normalize_identity_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if any(ch.isdigit() for ch in text):
        return "".join(ch for ch in text if ch.isdigit())
    return text


def _intent_relation(plan: dict[str, Any]) -> str:
    relation = str(plan.get("intent_relation") or "current_sop_supplement")
    return relation if relation in INTENT_RELATIONS else "unclear"


def _allowed_attachment_urls(state: dict[str, Any], intent: str, slot_memory: dict[str, Any]) -> set[str]:
    urls = {
        str(item["url"]) for item in _verified_receipt_attachments(intent, state.get("attachments") or []) if item.get("url")
    }
    for item in _verified_receipt_attachments(intent, state.get("attachments_summary") or []):
        if item.get("url"):
            urls.add(str(item["url"]))
    for key in ("receipt_screenshot", "deposit_screenshot", "withdrawal_screenshot"):
        if slot_memory.get(key):
            urls.add(str(slot_memory[key]))
    return urls


def _attachments_summary(state: dict[str, Any], intent: str | None = None) -> list[dict[str, Any]]:
    attachments = []
    for item in state.get("attachments") or []:
        normalized = _verified_receipt_attachment(intent or "", item) if intent else None
        source = normalized or item
        attachments.append(
            {
                "url": source.get("url"),
                "name": source.get("name"),
                "mime_type": source.get("mime_type"),
                "content_type": source.get("content_type"),
                "image_analysis_status": source.get("image_analysis_status"),
                "image_candidate_id": source.get("image_candidate_id"),
                "verified_receipt_attachment": source.get("verified_receipt_attachment"),
                "receipt_kind": source.get("receipt_kind"),
            }
        )
    return [
        {key: value for key, value in item.items() if value is not None}
        for item in attachments
    ]
