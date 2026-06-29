from app.llm.contracts import (
    LLMIntentShadowInput,
    LLMIntentShadowOutput,
    LLMRouterDecisionOutput,
    LLMRouterInput,
    LLMRewriteShadowInput,
    LLMRewriteShadowOutput,
    LLMSopSlotExtractionInput,
    LLMSopSlotExtractionOutput,
)
from app.workflows.sop_slot_extractor import extract_sop_slots
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
        router_mode = _router_mode_from_payload(payload)
        if router_mode == "faq_authoritative":
            return _faq_authoritative_route(payload, provider_name=self.provider_name)
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
            "mode": router_mode,
        }

    async def extract_sop_slots(self, payload: LLMSopSlotExtractionInput) -> LLMSopSlotExtractionOutput:
        result = extract_sop_slots(
            str(payload.get("intent") or ""),
            payload.get("current_slot_memory") or {},
            payload.get("latest_user_text") or "",
            payload.get("attachments_summary") or [],
        )
        return {
            "intent": result["intent"],
            "extracted_slots": result["extracted_slots"],
            "attachment_classification": result["attachment_classification"],
            "missing_slots": result["missing_slots"],
            "confidence": result["confidence"],
            "reason": "Mock SOP slot extractor mirrors deterministic fallback for offline validation.",
            "provider": self.provider_name,
            "mode": "sop_slot",
        }


def _router_mode_from_payload(payload: dict) -> str:
    mode = str(payload.get("router_mode") or payload.get("mode") or "guarded_authoritative").strip().lower()
    return mode if mode in {"guarded_authoritative", "faq_authoritative"} else "guarded_authoritative"


def _faq_authoritative_route(payload: LLMRouterInput, provider_name: str) -> LLMRouterDecisionOutput:
    text = normalize_text(payload.get("raw_user_input"))
    lower = text.lower()
    if any(token in lower for token in ("怎么存款", "如何充值", "deposit", "recharge")):
        intent = "deposit_howto"
        faq_query = "怎么存款"
    elif any(token in lower for token in ("如何提款", "withdraw")):
        intent = "withdrawal_howto"
        faq_query = "如何提款"
    elif any(token in lower for token in ("忘记密码", "forgot password", "reset password")):
        intent = "forgot_password_howto"
        faq_query = "忘记密码"
    else:
        return {
            "rewritten_question": text,
            "normalized_query": text,
            "language": "unknown",
            "intent": "clarification_needed",
            "route": "clarification",
            "confidence": 0.84,
            "sop_name": None,
            "faq_query": None,
            "risk_level": None,
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "Mock FAQ-authoritative router asks for clarification when no FAQ alias is matched.",
            "provider": provider_name,
            "mode": "faq_authoritative",
        }
    return {
        "rewritten_question": text,
        "normalized_query": faq_query,
        "language": "zh" if any("\u4e00" <= char <= "\u9fff" for char in text) else "unknown",
        "intent": intent,
        "route": "faq",
        "confidence": 0.9,
        "sop_name": None,
        "faq_query": faq_query,
        "risk_level": None,
        "requires_human": False,
        "requires_backend": False,
        "missing_slots": [],
        "preserved_entities": [],
        "reason": "Mock FAQ-authoritative router matched a FAQ alias.",
        "provider": provider_name,
        "mode": "faq_authoritative",
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
