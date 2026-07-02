from __future__ import annotations

from typing import Any

from app.workflows.sop_definitions import get_sop_definition


ALLOWED_LLM_ROUTES = (
    "faq",
    "sop",
    "human_handoff",
    "emotion_care",
    "final_reply",
)

CANONICAL_FAQ_INTENTS = (
    "deposit_howto",
    "withdrawal_howto",
    "forgot_password_howto",
    "screenshot_upload_howto",
)

WORKFLOW_RELATIONS_WITH_ACTIVE = (
    "current_workflow_supplement",
    "current_workflow_resolution",
    "independent_faq",
    "new_workflow_request",
    "human_escalation",
    "acknowledgement",
    "contextual_followup",
    "unclear",
)
WORKFLOW_RELATIONS_WITHOUT_ACTIVE = ("none",)

SOP_INTENTS = (
    "deposit_missing",
    "withdrawal_missing",
    "withdrawal_blocked_or_rollover",
    "pending_reply_lookup",
)

ALLOWED_LLM_INTENTS = (
    *CANONICAL_FAQ_INTENTS,
    "deposit_missing",
    "withdrawal_missing",
    "withdrawal_blocked_or_rollover",
    "pending_reply_lookup",
    "account_access_issue",
    "account_profile_or_wallet_change",
    "explicit_human_request",
    "service_frustration",
    "abusive_or_emotional",
    "unsupported_concrete_issue",
    "clarification_needed",
    "acknowledgement",
    "contextual_followup",
    "casual_chat",
    "backend_fact_like",
)

ALLOWED_RISK_FLAGS = (
    "active_workflow",
    "backend_fact_like",
    "user_fact_present",
    "attachment_present",
    "low_confidence",
    "unsupported_route",
    "unsupported_intent",
)

BACKEND_FACT_TOKENS = (
    "backend",
    "account",
    "order",
    "payment",
    "balance",
    "status",
    "deposit status",
    "withdrawal status",
    "order status",
    "deposito",
    "depósito",
    "retiro",
    "saldo",
    "estado",
    "orden",
    "pago",
    "pagaron",
    "no llegó",
    "no llego",
    "no acreditado",
    "未到账",
    "没到账",
    "提款状态",
    "订单",
    "余额",
    "支付",
    "付款",
)
_USER_FACT_TOKENS = (
    "amount",
    "金额",
    "monto",
    "usuario",
    "username",
    "phone",
    "telefono",
    "teléfono",
    "手机号",
)

SOP_SLOT_FIELDS = {
    "account_or_phone",
    "amount",
    "payment_channel",
    "order_id",
    "deposit_screenshot",
    "withdrawal_screenshot",
}
PROTECTED_SOP_SLOT_FIELDS = {
    "telegram_case_id",
    "telegram_message_id",
    "telegram_target_chat_id",
    "telegram_message_thread_id",
}


def normalize_confidence(value) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def validate_llm_route(route: str) -> str:
    normalized = normalize_llm_route(route)
    if normalized not in ALLOWED_LLM_ROUTES:
        raise ValueError(f"Unsupported llm route: {normalized or route}")
    return normalized


def normalize_llm_route(route: str) -> str:
    normalized = str(route or "").strip().lower().replace("-", "_")
    route_aliases = {
        "sop": "sop",
        "faq": "faq",
        "clarification": "final_reply",
        "contextual_reply": "final_reply",
        "casual_chat": "final_reply",
        "unsupported": "final_reply",
        "final": "final_reply",
        "final reply": "final_reply",
        "final_reply": "final_reply",
        "human": "human_handoff",
        "human handoff": "human_handoff",
        "human_handoff": "human_handoff",
    }
    return route_aliases.get(normalized, normalized)


def validate_llm_intent(intent: str) -> str:
    normalized = normalize_llm_intent(intent)
    if normalized not in ALLOWED_LLM_INTENTS:
        raise ValueError(f"Unsupported llm intent: {normalized or intent}")
    return normalized


def normalize_llm_intent(intent: str) -> str:
    normalized = str(intent or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "deposit_inquiry": "deposit_howto",
        "deposit": "deposit_howto",
        "deposit_guide": "deposit_howto",
        "recharge_howto": "deposit_howto",
        "recharge": "deposit_howto",
        "withdrawal_inquiry": "withdrawal_howto",
        "withdrawal": "withdrawal_howto",
        "withdraw": "withdrawal_howto",
        "withdraw_guide": "withdrawal_howto",
        "password_reset": "forgot_password_howto",
        "reset_password": "forgot_password_howto",
        "forgot_password": "forgot_password_howto",
    }
    return aliases.get(normalized, normalized)


def normalize_router_decision_intent(intent: str, route: str, requires_human: bool) -> str:
    normalized = normalize_llm_intent(intent)
    if normalized in ALLOWED_LLM_INTENTS:
        return normalized
    if route == "human_handoff" and requires_human:
        return "explicit_human_request"
    if route == "final_reply" and normalized in {"clarification", "clarification_needed", "unsupported", "unsupported_concrete_issue"}:
        return "clarification_needed"
    if route == "final_reply" and normalized in {"contextual_reply", "contextual_followup"}:
        return "contextual_followup"
    if route == "final_reply" and normalized == "casual_chat":
        return "casual_chat"
    if route == "final_reply":
        return "clarification_needed"
    raise ValueError(f"Unsupported llm intent: {normalized or intent}")


def validate_route_intent_pair(route: str, intent: str) -> None:
    if route == "faq" and intent not in CANONICAL_FAQ_INTENTS:
        raise ValueError(f"FAQ route requires a canonical FAQ intent: {intent}")


def normalize_risk_flags(flags: list[str] | None) -> list[str]:
    result = []
    seen = set()
    for flag in flags or []:
        normalized = str(flag or "").strip()
        if not normalized or normalized not in ALLOWED_RISK_FLAGS or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def enforce_rewrite_risk_flags(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    flags = list(output.get("risk_flags") or [])
    raw_user_input = str(payload.get("raw_user_input") or "")
    lowered = raw_user_input.lower()
    if payload.get("active_workflow"):
        flags.append("active_workflow")
    if contains_backend_fact_signal(lowered):
        flags.append("backend_fact_like")
    if _contains_user_fact_signal(lowered):
        flags.append("user_fact_present")
    if payload.get("attachments_summary"):
        flags.append("attachment_present")
    output["risk_flags"] = normalize_risk_flags(flags)
    return output


def validate_rewrite_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    rewritten_question = _require_str(output, "rewritten_question", "rewrite shadow")
    normalized_query = _require_str(output, "normalized_query", "rewrite shadow")
    reason = _require_str(output, "reason", "rewrite shadow")
    validated = {
        "rewritten_question": rewritten_question,
        "normalized_query": normalized_query,
        "language": str(output.get("language") or "unknown"),
        "preserved_entities": _string_list(output.get("preserved_entities")),
        "missing_or_ambiguous": _string_list(output.get("missing_or_ambiguous")),
        "risk_flags": normalize_risk_flags(output.get("risk_flags") or []),
        "confidence": normalize_confidence(output.get("confidence")),
        "reason": reason,
    }
    return enforce_rewrite_risk_flags(payload, validated)


def validate_intent_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    validated = {
        "intent": validate_llm_intent(_require_str(output, "intent", "intent shadow")),
        "route": validate_llm_route(_require_str(output, "route", "intent shadow")),
        "confidence": normalize_confidence(output.get("confidence")),
        "reason": _require_str(output, "reason", "intent shadow"),
        "sop_name": _optional_str(output.get("sop_name")),
        "faq_query": _optional_str(output.get("faq_query")),
        "risk_level": _optional_str(output.get("risk_level")),
    }
    validate_route_intent_pair(validated["route"], validated["intent"])
    return validated


def validate_router_decision_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    return validate_intent_classification_output(payload, output)


def validate_intent_classification_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    route = validate_llm_route(_require_str(output, "route", "router decision"))
    requires_human = True if route == "human_handoff" else bool(output.get("requires_human", False))
    intent = normalize_router_decision_intent(
        _require_str(output, "intent", "router decision"),
        route,
        requires_human,
    )
    validate_route_intent_pair(route, intent)
    workflow_relation = _validate_workflow_relation(payload, output.get("workflow_relation"), route)
    preserve_active_workflow = bool(output.get("preserve_active_workflow", True))
    if (
        payload.get("active_workflow")
        and route != "human_handoff"
        and workflow_relation != "current_workflow_resolution"
        and not preserve_active_workflow
    ):
        raise ValueError("Active workflow must be preserved during intent classification.")
    validated = {
        "intent": intent,
        "route": route,
        "confidence": normalize_confidence(output.get("confidence")),
        "sop_name": _optional_str(output.get("sop_name")),
        "faq_query": _optional_str(output.get("faq_query")),
        "risk_level": _optional_str(output.get("risk_level")),
        "requires_human": requires_human,
        "requires_backend": bool(output.get("requires_backend", False)),
        "missing_slots": _string_list(output.get("missing_slots")),
        "workflow_relation": workflow_relation,
        "preserve_active_workflow": preserve_active_workflow,
        "reason": _require_str(output, "reason", "router decision"),
    }
    _validate_intent_classification_contract(payload, validated)
    return validated


def _validate_workflow_relation(payload: dict[str, Any], value, route: str) -> str | None:
    active_workflow = _optional_str(payload.get("active_workflow"))
    relation = _optional_str(value)
    if active_workflow:
        if not relation:
            raise ValueError("workflow_relation is required when active_workflow is present.")
        if relation not in WORKFLOW_RELATIONS_WITH_ACTIVE:
            raise ValueError(f"Unsupported workflow_relation with active workflow: {relation}")
        return relation
    if relation is None:
        return None
    if relation not in WORKFLOW_RELATIONS_WITHOUT_ACTIVE:
        raise ValueError(f"workflow_relation without active workflow must be none or null: {relation}")
    return relation


def _validate_intent_classification_contract(payload: dict[str, Any], output: dict[str, Any]) -> None:
    active_workflow = _optional_str(payload.get("active_workflow"))
    route = output["route"]
    intent = output["intent"]
    relation = output.get("workflow_relation")
    if route == "faq":
        if intent not in CANONICAL_FAQ_INTENTS:
            raise ValueError(f"FAQ route requires a canonical FAQ intent: {intent}")
        if not output.get("faq_query"):
            raise ValueError("FAQ route requires faq_query.")
        if output.get("requires_backend"):
            raise ValueError("FAQ route cannot require backend facts.")
        if output.get("requires_human"):
            raise ValueError("FAQ route cannot require human handoff.")
        if output.get("missing_slots"):
            raise ValueError("FAQ route cannot request SOP slots.")
        if output.get("sop_name"):
            raise ValueError("FAQ route cannot set sop_name.")
    if route == "sop":
        sop_name = _optional_str(output.get("sop_name"))
        if sop_name and not get_sop_definition(sop_name):
            raise ValueError(f"Unsupported SOP name: {sop_name}")
        if (
            intent in SOP_INTENTS
            and not output.get("requires_backend")
            and relation not in {"current_workflow_supplement", "current_workflow_resolution"}
        ):
            raise ValueError("SOP route for backend workflows must set requires_backend=true.")
    if route == "human_handoff" and not output.get("requires_human"):
        raise ValueError("human_handoff route must set requires_human=true.")
    if route == "final_reply" and intent not in {
        "acknowledgement",
        "contextual_followup",
        "casual_chat",
        "clarification_needed",
        "unsupported_concrete_issue",
    }:
        raise ValueError("final_reply route requires a final-reply intent.")
    if not active_workflow:
        return
    if relation == "current_workflow_supplement":
        if route != "sop":
            raise ValueError("current_workflow_supplement requires route=sop.")
        if intent != active_workflow:
            raise ValueError("current_workflow_supplement intent must match active_workflow.")
        sop_name = _optional_str(output.get("sop_name"))
        if sop_name and sop_name != active_workflow:
            raise ValueError("current_workflow_supplement sop_name must match active_workflow.")
        if _business_object_conflicts_with_active_workflow(payload, active_workflow):
            raise ValueError("current_workflow_supplement business object conflicts with active_workflow.")
    elif relation == "current_workflow_resolution":
        if route != "sop":
            raise ValueError("current_workflow_resolution requires route=sop.")
        if intent != active_workflow:
            raise ValueError("current_workflow_resolution intent must match active_workflow.")
        sop_name = _optional_str(output.get("sop_name"))
        if sop_name and sop_name != active_workflow:
            raise ValueError("current_workflow_resolution sop_name must match active_workflow.")
        if output.get("preserve_active_workflow"):
            raise ValueError("current_workflow_resolution must not preserve active workflow.")
        if _business_object_conflicts_with_active_workflow(payload, active_workflow):
            raise ValueError("current_workflow_resolution business object conflicts with active_workflow.")
    elif relation == "independent_faq":
        if route != "faq":
            raise ValueError("independent_faq requires route=faq.")
        if not output.get("preserve_active_workflow"):
            raise ValueError("independent_faq must preserve active workflow.")
    elif relation == "new_workflow_request":
        if route != "final_reply" or intent != "clarification_needed":
            raise ValueError("new_workflow_request must ask clarification before switching workflow.")
        if not output.get("preserve_active_workflow"):
            raise ValueError("new_workflow_request must preserve active workflow.")
    elif relation == "human_escalation":
        if route != "human_handoff":
            raise ValueError("human_escalation requires route=human_handoff.")
    elif relation == "acknowledgement":
        if route != "final_reply" or intent != "acknowledgement":
            raise ValueError("acknowledgement requires final_reply route.")
        if not output.get("preserve_active_workflow"):
            raise ValueError("acknowledgement must preserve active workflow.")
    elif relation == "contextual_followup":
        if route != "final_reply" or intent != "contextual_followup":
            raise ValueError("contextual_followup requires final_reply route.")
        if not output.get("preserve_active_workflow"):
            raise ValueError("contextual_followup must preserve active workflow.")
    elif relation == "unclear":
        if route != "final_reply":
            raise ValueError("unclear workflow relation requires route=final_reply.")
        if not output.get("preserve_active_workflow"):
            raise ValueError("unclear workflow relation must preserve active workflow.")


def _business_object_conflicts_with_active_workflow(payload: dict[str, Any], active_workflow: str) -> bool:
    text = " ".join(
        str(payload.get(key) or "").lower()
        for key in ("rewritten_question", "raw_user_input", "latest_user_text")
    )
    if not text:
        return False
    mentions_deposit = any(token in text for token in ("deposit", "depósito", "deposito", "存款", "充值"))
    mentions_withdrawal = any(token in text for token in ("withdraw", "withdrawal", "retiro", "提款", "提现"))
    if active_workflow == "withdrawal_missing" and mentions_deposit and not mentions_withdrawal:
        return True
    if active_workflow == "deposit_missing" and mentions_withdrawal and not mentions_deposit:
        return True
    return False


def validate_sop_slot_extraction_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    expected_intent = str(payload.get("intent") or "")
    output_intent = _require_str(output, "intent", "sop slot extraction")
    if output_intent != expected_intent:
        raise ValueError(f"SOP slot extraction intent mismatch: {output_intent} != {expected_intent}")
    reason = _require_str(output, "reason", "sop slot extraction")
    allowed_urls = _allowed_attachment_urls(payload)
    visible_text = _visible_sop_text(payload)
    extracted = {}
    confidence = {}
    dropped_fields = []
    for key, value in dict(output.get("extracted_slots") or {}).items():
        if key in PROTECTED_SOP_SLOT_FIELDS:
            dropped_fields.append(key)
            continue
        if key not in SOP_SLOT_FIELDS:
            dropped_fields.append(key)
            continue
        normalized_value = _optional_str(value)
        if normalized_value is None:
            extracted[key] = None
            confidence[key] = normalize_confidence((output.get("confidence") or {}).get(key))
            continue
        if key in {"deposit_screenshot", "withdrawal_screenshot"}:
            if normalized_value not in allowed_urls:
                extracted[key] = None
                confidence[key] = 0.0
                dropped_fields.append(key)
                continue
        elif not _text_value_is_visible(normalized_value, visible_text):
            extracted[key] = None
            confidence[key] = 0.0
            dropped_fields.append(key)
            continue
        extracted[key] = normalized_value
        confidence[key] = normalize_confidence((output.get("confidence") or {}).get(key))

    slot_memory = {**(payload.get("current_slot_memory") or {}), **{k: v for k, v in extracted.items() if v}}
    from app.workflows.llm_sop_dialogue_planner import compute_missing_slots

    missing_slots = _legacy_sop_missing_slots(expected_intent, compute_missing_slots(expected_intent, slot_memory))
    attachment_classification = {
        key: value
        for key, value in dict(output.get("attachment_classification") or {}).items()
        if key in {"deposit_screenshot", "withdrawal_screenshot", "unknown_attachments"}
    }
    for key in ("deposit_screenshot", "withdrawal_screenshot"):
        if attachment_classification.get(key) not in allowed_urls:
            attachment_classification[key] = None
    return {
        "intent": expected_intent,
        "extracted_slots": extracted,
        "attachment_classification": attachment_classification,
        "missing_slots": missing_slots,
        "confidence": confidence,
        "reason": reason,
        "dropped_fields": dropped_fields,
    }


def validate_sop_dialogue_planner_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    from app.workflows.llm_sop_dialogue_planner import apply_llm_sop_plan

    intent = str(payload.get("sop_name") or payload.get("intent") or "")
    normalized = {
        "status": "accepted",
        "intent_relation": _optional_str(output.get("intent_relation")) or "unclear",
        "extracted_slots": dict(output.get("extracted_slots") or {}),
        "slot_updates": dict(output.get("slot_updates") or output.get("extracted_slots") or {}),
        "slot_confidence": dict(output.get("slot_confidence") or output.get("confidence") or {}),
        "missing_slots": _string_list(output.get("missing_slots")),
        "should_ask_confirmation": bool(output.get("should_ask_confirmation")),
        "reply_draft": str(output.get("reply_draft") or ""),
        "reason": _require_str(output, "reason", "sop dialogue planner"),
    }
    state = {
        "slot_memory": dict(payload.get("current_slot_memory") or {}),
        "attachments": list(payload.get("attachments_summary") or []),
        "attachments_summary": list(payload.get("attachments_summary") or []),
    }
    planned = apply_llm_sop_plan(state, intent, normalized)
    return {
        "intent_relation": planned["intent_relation"],
        "extracted_slots": normalized["extracted_slots"],
        "slot_updates": planned["slot_updates"],
        "slot_confidence": planned["slot_confidence"],
        "missing_slots": planned["missing_slots"],
        "should_ask_confirmation": planned["should_ask_confirmation"],
        "reply_draft": planned["reply_draft"],
        "reason": planned["reason"],
        "dropped_slots": planned["dropped_slots"],
    }


def _require_str(output: dict[str, Any], field: str, output_name: str) -> str:
    value = output.get(field)
    if value is None:
        raise ValueError(f"Missing required {output_name} field: {field}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"Missing required {output_name} field: {field}")
    return text


def _legacy_sop_missing_slots(intent: str, missing_slots: list[str]) -> list[str]:
    screenshot_key = "deposit_screenshot" if intent == "deposit_missing" else "withdrawal_screenshot"
    aliases = {"phone": "account_or_phone", "receipt_screenshot": screenshot_key}
    return [aliases.get(slot, slot) for slot in missing_slots]


def _optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _allowed_attachment_urls(payload: dict[str, Any]) -> set[str]:
    urls = {
        str(item.get("url"))
        for item in payload.get("attachments_summary") or []
        if item.get("url")
    }
    current = payload.get("current_slot_memory") or {}
    for key in ("deposit_screenshot", "withdrawal_screenshot"):
        if current.get(key):
            urls.add(str(current[key]))
    return urls


def _visible_sop_text(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("latest_user_text") or "")]
    for message in payload.get("recent_messages") or []:
        parts.append(str(message.get("text_content") or message.get("text") or message.get("content") or ""))
    return "\n".join(parts).lower()


def _text_value_is_visible(value: str, visible_text: str) -> bool:
    lowered = value.lower()
    if lowered in visible_text:
        return True
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits and digits in "".join(ch for ch in visible_text if ch.isdigit()):
        return True
    return False


def _string_list(value) -> list[str]:
    if not value:
        return []
    return [str(item) for item in value if str(item).strip()]


def contains_backend_fact_signal(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in BACKEND_FACT_TOKENS)


def _contains_user_fact_signal(lowered: str) -> bool:
    return any(token in lowered for token in _USER_FACT_TOKENS)
