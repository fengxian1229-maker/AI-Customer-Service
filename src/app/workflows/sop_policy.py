from typing import Any

from app.workflows.sop_definitions import get_sop_definition
from app.workflows.llm_sop_dialogue_planner import compute_missing_slots
from app.workflows.slot_extractors import (
    extract_identity,
    extract_order_id,
    extract_transaction_signal,
    is_explicit_human_request,
)


def evaluate_sop_policy(
    intent: str | None,
    slot_memory: dict[str, Any],
    conversation_status: str | None = None,
    active_workflow: str | None = None,
    workflow_stage: str | None = None,
    latest_text: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    definition = get_sop_definition(intent)
    if definition is None:
        return {"action": "blocked", "missing_slots": [], "reason": "unsupported_sop_intent", "allowed": False}
    if conversation_status == "HUMAN_ACTIVE":
        return {"action": "blocked", "missing_slots": [], "reason": "conversation_is_human_active", "allowed": False}

    missing_slots = compute_missing_slots(str(intent or ""), slot_memory)
    stage = workflow_stage or "collecting_slots"
    if stage == "waiting_backend":
        text = str(latest_text or "")
        has_supplement = bool(
            _has_verified_receipt_supplement(str(intent or ""), attachments or [])
            or extract_identity(text)
            or extract_order_id(text)
            or extract_transaction_signal(text)
        )
        explicit_human = is_explicit_human_request(text)
        if has_supplement:
            if slot_memory.get("telegram_case_id") and slot_memory.get("telegram_message_id"):
                return {"action": "append_to_case", "missing_slots": [], "reason": "customer_sent_supplement", "allowed": True}
            if explicit_human:
                return {
                    "action": "human_handoff",
                    "missing_slots": [],
                    "reason": "customer_requested_human_after_supplement",
                    "allowed": True,
                }
            return {"action": "waiting_followup", "missing_slots": [], "reason": "case_not_created_yet", "allowed": True}
        if explicit_human:
            return {"action": "human_handoff", "missing_slots": [], "reason": "customer_requested_human", "allowed": True}
        return {"action": "waiting_followup", "missing_slots": [], "reason": "customer_asked_status_or_unclear", "allowed": True}

    if missing_slots:
        return {"action": "ask_missing_slots", "missing_slots": missing_slots, "reason": "required_slots_missing", "allowed": True}
    if slot_memory.get("telegram_case_id") or slot_memory.get("telegram_message_id"):
        return {"action": "waiting_followup", "missing_slots": [], "reason": "telegram_case_already_exists", "allowed": True}
    if active_workflow and active_workflow != intent:
        return {"action": "blocked", "missing_slots": [], "reason": "active_workflow_mismatch", "allowed": False}
    return {"action": "send_telegram_case", "missing_slots": [], "reason": "required_slots_complete", "allowed": True}


def _has_verified_receipt_supplement(intent: str, attachments: list[dict[str, Any]]) -> bool:
    expected_kind = "deposit" if intent == "deposit_missing" else "withdrawal" if intent == "withdrawal_missing" else None
    if expected_kind is None:
        return False
    return any(
        attachment.get("url")
        and attachment.get("verified_receipt_attachment")
        and str(attachment.get("receipt_kind") or "").lower() == expected_kind
        for attachment in attachments
    )
