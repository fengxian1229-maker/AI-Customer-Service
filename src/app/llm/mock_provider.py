from app.llm.contracts import (
    LLMIntentShadowInput,
    LLMIntentShadowOutput,
    LLMRouterDecisionOutput,
    LLMRouterInput,
    LLMRewriteShadowInput,
    LLMRewriteShadowOutput,
)
from app.workflows.slot_extractors import extract_identity, extract_order_id, normalize_text


class MockLLMProvider:
    provider_name = "mock"

    async def rewrite(self, payload: LLMRewriteShadowInput) -> LLMRewriteShadowOutput:
        text = normalize_text(payload.get("raw_user_input"))
        active_workflow = payload.get("active_workflow")
        preserved_entities = []
        identity = extract_identity(text)
        order_id = extract_order_id(text)
        if identity:
            preserved_entities.append(identity["value"])
        if order_id:
            preserved_entities.append(order_id)

        return {
            "rewritten_question": payload.get("current_rewritten_question") or text,
            "normalized_query": payload.get("current_rewritten_question") or text,
            "language": ((payload.get("deterministic_rewrite_result") or {}).get("language")) or "unknown",
            "preserved_entities": preserved_entities,
            "missing_or_ambiguous": ["supplement_context"] if active_workflow else [],
            "risk_flags": _risk_flags(text, active_workflow=active_workflow),
            "confidence": 0.82,
            "reason": "Mock shadow rewrite mirrors deterministic text and annotates risk only.",
            "provider": self.provider_name,
            "mode": "shadow",
        }

    async def classify_intent(self, payload: LLMIntentShadowInput) -> LLMIntentShadowOutput:
        deterministic = payload.get("deterministic_intent_result") or {}
        text = normalize_text(payload.get("raw_user_input"))
        return {
            "intent": deterministic.get("intent") or "faq_general",
            "route": deterministic.get("route") or payload.get("deterministic_route") or "faq",
            "confidence": 0.84,
            "reason": "Mock shadow intent mirrors deterministic decision for offline validation.",
            "sop_name": deterministic.get("sop_name"),
            "faq_query": deterministic.get("faq_query"),
            "risk_level": "elevated" if _risk_flags(text) else None,
            "provider": self.provider_name,
            "mode": "shadow",
        }

    async def route(self, payload: LLMRouterInput) -> LLMRouterDecisionOutput:
        deterministic = payload.get("deterministic_intent_result") or {}
        rewrite = payload.get("deterministic_rewrite_result") or {}
        text = normalize_text(payload.get("raw_user_input"))
        return {
            "rewritten_question": rewrite.get("rewritten_question") or text,
            "normalized_query": rewrite.get("rewritten_question") or text,
            "language": rewrite.get("language") or "unknown",
            "intent": deterministic.get("intent") or "faq_general",
            "route": deterministic.get("route") or payload.get("deterministic_route") or "faq",
            "confidence": 0.84,
            "sop_name": deterministic.get("sop_name"),
            "faq_query": deterministic.get("faq_query"),
            "risk_level": "elevated" if _risk_flags(text) else None,
            "requires_human": (deterministic.get("route") == "human_handoff"),
            "requires_backend": bool(_risk_flags(text)),
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "Mock guarded router mirrors deterministic decision for offline validation.",
            "provider": self.provider_name,
            "mode": "guarded_authoritative",
        }


def _risk_flags(text: str, active_workflow: str | None = None) -> list[str]:
    lower = text.lower()
    flags = []
    if active_workflow:
        flags.append("active_workflow")
    if any(token in lower for token in ("balance", "status", "estado", "订单", "order", "withdrawal", "retiro", "未到账", "没到账")):
        flags.append("backend_fact_like")
    if any(token in lower for token in ("amount", "金额", "monto", "usuario", "phone", "telefono", "手机号")):
        flags.append("user_fact_present")
    return flags
