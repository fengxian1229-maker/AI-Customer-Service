import json
from datetime import date, datetime
from decimal import Decimal

from app.llm.contracts import (
    LLMIntentShadowInput,
    LLMIntentShadowOutput,
    LLMIntentShadowSchema,
    LLMIntentClassificationOutput,
    LLMIntentClassificationInput,
    LLMIntentClassificationSchema,
    LLMImageAttachmentAnalysisInput,
    LLMImageAttachmentAnalysisOutput,
    LLMImageAttachmentAnalysisSchema,
    LLMSopDialoguePlannerInput,
    LLMSopDialoguePlannerOutput,
    LLMSopDialoguePlannerSchema,
    LLMRewriteAuthoritativeSchema,
    LLMRewriteShadowInput,
    LLMRewriteShadowOutput,
    LLMSopSlotExtractionInput,
    LLMSopSlotExtractionOutput,
    LLMSopSlotExtractionSchema,
)
from app.llm.guardrails import (
    validate_intent_output,
    validate_rewrite_output,
    validate_router_decision_output,
    validate_sop_dialogue_planner_output,
    validate_sop_slot_extraction_output,
)
from app.llm.gemini_model import build_gemini_chat_model
from app.prompts.loader import load_prompt, render_prompt

REWRITE_SYSTEM_PROMPT = load_prompt("rewrite_system.md")

INTENT_SYSTEM_PROMPT = load_prompt("intent_shadow_system.md")

FAQ_KNOWLEDGE_TARGETS = """FAQ knowledge targets. For FAQ route, choose the closest target and set intent and faq_query exactly as specified unless the question is genuinely outside the list:

1. 充值教程
   - route: faq
   - intent: deposit_howto
   - faq_query: 怎么存款
   - Use for: how to add money, fund account, put money into account, start playing after adding money, recharge, top up, cash in, deposit tutorial.

2. 提款教程
   - route: faq
   - intent: withdrawal_howto
   - faq_query: 如何提款
   - Use for: how to withdraw, withdrawal tutorial, cash out guide.

3. 忘记密码说明
   - route: faq
   - intent: forgot_password_howto
   - faq_query: 忘记密码
   - Use for: forgot password, reset password, cannot remember password.

4. 上传截图说明
   - route: faq
   - intent: screenshot_upload_howto
   - faq_query: 上传截图
   - Use for: how to upload/send screenshot or proof image.
"""

ALLOWED_INTENT_CONTRACT = """Allowed intents. The intent field must be exactly one of these values. Do not invent, translate, rename, or paraphrase intent names:
- deposit_howto
- withdrawal_howto
- forgot_password_howto
- screenshot_upload_howto
- deposit_missing
- withdrawal_missing
- withdrawal_blocked_or_rollover
- pending_reply_lookup
- account_access_issue
- account_profile_or_wallet_change
- screenshot_upload_failed
- wallet_identity_risk
- account_verification_issue
- promo_refund_unsupported
- game_technical_issue
- abuse_or_fraud_risk
- tutorial_failed_aftercare
- active_workflow_conflict_with_data
- menu_stuck_repeated
- waiting_backend_repeat_dispute
- explicit_human_request
- service_frustration
- abusive_or_emotional
- unsupported_concrete_issue
- clarification_needed
- acknowledgement
- contextual_followup
- conversation_memory_lookup
- casual_chat
- backend_fact_like
"""

GUARDED_AUTHORITATIVE_INTENT_CLASSIFIER_SYSTEM_PROMPT = render_prompt(
    "intent_router_system.md",
    allowed_intent_contract=ALLOWED_INTENT_CONTRACT,
    faq_knowledge_targets=FAQ_KNOWLEDGE_TARGETS,
)

GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_INTENT_CLASSIFIER_SYSTEM_PROMPT
FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT

SOP_SLOT_EXTRACTOR_SYSTEM_PROMPT = load_prompt("sop_slot_extractor_system.md")

SOP_DIALOGUE_PLANNER_SYSTEM_PROMPT = load_prompt("sop_dialogue_planner_system.md")

IMAGE_ATTACHMENT_ANALYSIS_SYSTEM_PROMPT = load_prompt("image_analysis_system.md")


class GeminiLLMProvider:
    provider_name = "gemini"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = build_gemini_chat_model(self.settings)
        return self._model

    async def rewrite(self, payload: LLMRewriteShadowInput) -> LLMRewriteShadowOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMRewriteAuthoritativeSchema,
            method="json_schema",
        )
        raw = _model_dump(await structured_model.ainvoke(_build_chat_messages(REWRITE_SYSTEM_PROMPT, payload)))
        language = str(raw.get("detected_language") or raw.get("language") or "unknown")
        result = validate_rewrite_output(payload, {**raw, "language": language})
        return {
            "rewritten_question": result["rewritten_question"],
            "normalized_query": result["normalized_query"],
            "detected_language": language,
            "language": language,
            "language_confidence": float(raw.get("language_confidence") or 0.0),
            "preserved_entities": list(result.get("preserved_entities") or []),
            "missing_or_ambiguous": list(result.get("missing_or_ambiguous") or []),
            "risk_flags": list(result.get("risk_flags") or []),
            "confidence": float(result.get("confidence") or 0.0),
            "reason": result["reason"],
            "provider": self.provider_name,
            "mode": "shadow",
        }

    async def classify_intent(self, payload: LLMIntentShadowInput) -> LLMIntentShadowOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMIntentShadowSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(INTENT_SYSTEM_PROMPT, payload))
        result = validate_intent_output(payload, _model_dump(response))
        return {
            "intent": result["intent"],
            "route": result["route"],
            "confidence": float(result.get("confidence") or 0.0),
            "reason": result["reason"],
            "sop_name": result.get("sop_name"),
            "faq_query": result.get("faq_query"),
            "risk_level": result.get("risk_level"),
            "provider": self.provider_name,
            "mode": "shadow",
        }

    async def route(self, payload: LLMIntentClassificationInput) -> LLMIntentClassificationOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMIntentClassificationSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(ROUTER_SYSTEM_PROMPT, payload))
        result = validate_router_decision_output(payload, _model_dump(response))
        mode = str(payload.get("router_mode") or "guarded_authoritative")
        return {
            **result,
            "provider": self.provider_name,
            "mode": mode,
        }

    async def extract_sop_slots(self, payload: LLMSopSlotExtractionInput) -> LLMSopSlotExtractionOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMSopSlotExtractionSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(SOP_SLOT_EXTRACTOR_SYSTEM_PROMPT, payload))
        result = validate_sop_slot_extraction_output(payload, _model_dump(response))
        return {
            **result,
            "provider": self.provider_name,
            "mode": "sop_slot",
        }

    async def plan_sop_dialogue(self, payload: LLMSopDialoguePlannerInput) -> LLMSopDialoguePlannerOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMSopDialoguePlannerSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(SOP_DIALOGUE_PLANNER_SYSTEM_PROMPT, payload))
        result = validate_sop_dialogue_planner_output(payload, _model_dump(response))
        return {
            **result,
            "provider": self.provider_name,
            "mode": "sop_dialogue_planner",
        }

    async def analyze_image_attachment(self, payload: LLMImageAttachmentAnalysisInput) -> LLMImageAttachmentAnalysisOutput:
        attachment_url = str(payload.get("attachment_url") or "")
        if not attachment_url:
            return _unknown_image_analysis_result("missing_attachment_url", provider=self.provider_name)
        structured_model = self.model.with_structured_output(
            schema=LLMImageAttachmentAnalysisSchema,
            method="json_schema",
        )
        try:
            response = await structured_model.ainvoke(_build_image_analysis_messages(IMAGE_ATTACHMENT_ANALYSIS_SYSTEM_PROMPT, payload))
        except Exception:
            return _unknown_image_analysis_result("image_download_or_multimodal_error", provider=self.provider_name)
        raw = _model_dump(response)
        safety_flags = list(dict.fromkeys([*list(raw.get("safety_flags") or []), "candidate_only"]))
        return {
            "candidate_intents": list(raw.get("candidate_intents") or ["unknown_image"]),
            "candidate_slots": dict(raw.get("candidate_slots") or {}),
            "receipt_kind": str(raw.get("receipt_kind") or "unknown"),
            "is_receipt_like": bool(raw.get("is_receipt_like")),
            "confidence": float(raw.get("confidence") or 0.0),
            "evidence_summary": str(raw.get("evidence_summary") or ""),
            "safety_flags": safety_flags,
            "provider": self.provider_name,
            "mode": "image_analysis_candidate",
        }


def _model_dump(response) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    raise TypeError("Gemini structured output must be a dict-like schema result.")


def _build_chat_messages(system_prompt: str, payload: dict) -> list[tuple[str, str]]:
    return [
        ("system", system_prompt),
        ("human", _json_dumps_payload(payload)),
    ]


def _build_image_analysis_messages(system_prompt: str, payload: dict) -> list[tuple[str, str | list[dict]]]:
    metadata = {
        "attachment_url": payload.get("attachment_url"),
        "mime_type": payload.get("mime_type"),
        "filename": payload.get("filename"),
        "tenant_id": payload.get("tenant_id"),
        "conversation_id": payload.get("conversation_id"),
        "active_workflow": payload.get("active_workflow"),
        "workflow_stage": payload.get("workflow_stage"),
    }
    return [
        ("system", system_prompt),
        (
            "human",
            [
                {"type": "text", "text": _json_dumps_payload(metadata)},
                {"type": "image_url", "image_url": {"url": str(payload.get("attachment_url") or "")}},
            ],
        ),
    ]


def _unknown_image_analysis_result(reason: str, *, provider: str) -> LLMImageAttachmentAnalysisOutput:
    return {
        "candidate_intents": ["unknown_image"],
        "candidate_slots": {},
        "receipt_kind": "unknown",
        "is_receipt_like": False,
        "confidence": 0.0,
        "evidence_summary": "",
        "safety_flags": [reason, "candidate_only"],
        "provider": provider,
        "mode": "image_analysis_candidate",
    }


def _json_dumps_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
