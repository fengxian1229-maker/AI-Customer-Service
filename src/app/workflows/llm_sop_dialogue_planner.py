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
    "faq_interrupt",
    "new_issue",
    "human_request",
    "unclear",
}

LOW_CONFIDENCE_THRESHOLD = 0.55


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
        "attachments_summary": _attachments_summary(state),
        "recent_messages": list(state.get("recent_messages") or []),
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
    slot_updates: dict[str, Any] = {}
    dropped_slots: list[str] = list(dict.fromkeys(str(key) for key in llm_plan.get("dropped_slots") or []))
    allowed_keys = set(definition.slot_keys) | _legacy_schema_keys(intent)
    allowed_attachment_urls = _allowed_attachment_urls(state, slot_memory)

    for key, value in dict(llm_plan.get("slot_updates") or llm_plan.get("extracted_slots") or {}).items():
        key = str(key)
        if key in PROTECTED_SLOT_KEYS or key not in allowed_keys:
            dropped_slots.append(key)
            continue
        if value in (None, ""):
            continue
        if _slot_type(definition, key) == "attachment" and str(value) not in allowed_attachment_urls:
            dropped_slots.append(key)
            continue
        slot_updates[key] = value

    slot_memory.update(slot_updates)
    _merge_attachment_slots(intent, slot_memory, state.get("attachments") or [])
    _sync_legacy_aliases(intent, slot_memory)
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
        if value and float(confidence.get(key) or 1.0) < LOW_CONFIDENCE_THRESHOLD:
            return True
    return False


def _legacy_schema_keys(intent: str) -> set[str]:
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    return {"account_or_phone", screenshot_key, "channel", "deposit_order_id", "withdrawal_order_id"}


def _slot_type(definition: SopDefinition, key: str) -> str:
    if key in {"deposit_screenshot", "withdrawal_screenshot"}:
        return "attachment"
    for slot in definition.slots:
        if slot.key == key:
            return slot.type
    return "text"


def _merge_attachment_slots(intent: str, slot_memory: dict[str, Any], attachments: list[dict[str, Any]]) -> None:
    urls = attachment_urls(attachments)
    if not urls:
        return
    forwarded = list(dict.fromkeys([*slot_memory.get("forwarded_attachment_urls", []), *urls]))
    slot_memory["forwarded_attachment_urls"] = forwarded
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    slot_memory.setdefault("receipt_screenshot", urls[0])
    slot_memory.setdefault(screenshot_key, urls[0])


def _sync_legacy_aliases(intent: str, slot_memory: dict[str, Any]) -> None:
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    order_key = "deposit_order_id" if intent == "deposit_missing" else "withdrawal_order_id"
    if slot_memory.get("phone"):
        slot_memory["account_or_phone"] = slot_memory["phone"]
    elif slot_memory.get("account_or_phone"):
        slot_memory.setdefault("phone", slot_memory["account_or_phone"])
    if slot_memory.get("receipt_screenshot"):
        slot_memory.setdefault(screenshot_key, slot_memory["receipt_screenshot"])
    elif slot_memory.get(screenshot_key):
        slot_memory.setdefault("receipt_screenshot", slot_memory[screenshot_key])
    if slot_memory.get("order_id"):
        slot_memory.setdefault(order_key, slot_memory["order_id"])
    if slot_memory.get("payment_channel"):
        slot_memory.setdefault("channel", slot_memory["payment_channel"])


def _slot_present(intent: str, key: str, slot_memory: dict[str, Any]) -> bool:
    if key == "phone":
        return bool(slot_memory.get("phone") or slot_memory.get("account_or_phone"))
    if key == "receipt_screenshot":
        screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
        return bool(slot_memory.get("receipt_screenshot") or slot_memory.get(screenshot_key))
    return bool(slot_memory.get(key))


def _intent_relation(plan: dict[str, Any]) -> str:
    relation = str(plan.get("intent_relation") or "current_sop_supplement")
    return relation if relation in INTENT_RELATIONS else "unclear"


def _allowed_attachment_urls(state: dict[str, Any], slot_memory: dict[str, Any]) -> set[str]:
    urls = set(attachment_urls(state.get("attachments") or []))
    for item in state.get("attachments_summary") or []:
        if item.get("url"):
            urls.add(str(item["url"]))
    for key in ("receipt_screenshot", "deposit_screenshot", "withdrawal_screenshot"):
        if slot_memory.get(key):
            urls.add(str(slot_memory[key]))
    return urls


def _attachments_summary(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"url": item.get("url"), "name": item.get("name")} for item in state.get("attachments") or []]
