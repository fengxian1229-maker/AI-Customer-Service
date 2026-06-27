from __future__ import annotations

from typing import Any


ALLOWED_LLM_ROUTES = (
    "faq",
    "sop",
    "faq_then_sop",
    "human_handoff",
    "emotion_care",
    "clarification",
    "unsupported",
)

ALLOWED_LLM_INTENTS = (
    "faq_general",
    "deposit_howto",
    "deposit_inquiry",
    "withdrawal_howto",
    "forgot_password_howto",
    "screenshot_upload_howto",
    "rollover_explanation",
    "menu_help",
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
    normalized = str(route or "").strip().lower()
    route_aliases = {
        "sop": "sop",
        "faq": "faq",
        "clarification": "clarification",
        "unsupported": "unsupported",
        "human": "human_handoff",
        "human_handoff": "human_handoff",
    }
    normalized = route_aliases.get(normalized, normalized)
    if normalized not in ALLOWED_LLM_ROUTES:
        raise ValueError(f"Unsupported llm route: {normalized or route}")
    return normalized


def validate_llm_intent(intent: str) -> str:
    normalized = str(intent or "").strip()
    if normalized not in ALLOWED_LLM_INTENTS:
        raise ValueError(f"Unsupported llm intent: {normalized or intent}")
    return normalized


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
    return validated


def validate_router_decision_output(payload: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    del payload
    return {
        "rewritten_question": _require_str(output, "rewritten_question", "router decision"),
        "normalized_query": _optional_str(output.get("normalized_query")),
        "language": str(output.get("language") or "unknown"),
        "intent": validate_llm_intent(_require_str(output, "intent", "router decision")),
        "route": validate_llm_route(_require_str(output, "route", "router decision")),
        "confidence": normalize_confidence(output.get("confidence")),
        "sop_name": _optional_str(output.get("sop_name")),
        "faq_query": _optional_str(output.get("faq_query")),
        "risk_level": _optional_str(output.get("risk_level")),
        "requires_human": bool(output.get("requires_human", False)),
        "requires_backend": bool(output.get("requires_backend", False)),
        "missing_slots": _string_list(output.get("missing_slots")),
        "preserved_entities": _string_list(output.get("preserved_entities")),
        "reason": _require_str(output, "reason", "router decision"),
    }


def _require_str(output: dict[str, Any], field: str, output_name: str) -> str:
    value = output.get(field)
    if value is None:
        raise ValueError(f"Missing required {output_name} field: {field}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"Missing required {output_name} field: {field}")
    return text


def _optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value) -> list[str]:
    if not value:
        return []
    return [str(item) for item in value if str(item).strip()]


def contains_backend_fact_signal(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in BACKEND_FACT_TOKENS)


def _contains_user_fact_signal(lowered: str) -> bool:
    return any(token in lowered for token in _USER_FACT_TOKENS)
