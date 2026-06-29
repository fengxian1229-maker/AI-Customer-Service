import json
from datetime import date, datetime
from decimal import Decimal

from app.llm.contracts import (
    LLMIntentShadowInput,
    LLMIntentShadowOutput,
    LLMIntentShadowSchema,
    LLMRouterDecisionOutput,
    LLMRouterDecisionSchema,
    LLMRouterInput,
    LLMRewriteShadowInput,
    LLMRewriteShadowOutput,
    LLMRewriteShadowSchema,
)
from app.llm.guardrails import validate_intent_output, validate_rewrite_output, validate_router_decision_output
from app.llm.gemini_model import build_gemini_chat_model

REWRITE_SYSTEM_PROMPT = """You are a rewrite shadow model for an AI customer service routing system.
Your task is to normalize the user's message for downstream routing and retrieval.
You must preserve user-provided facts exactly, including usernames, phone numbers, order IDs, amounts, dates, and attachment references.
Do not invent facts.
Do not answer the customer.
Do not decide real backend/account/payment/order facts.
Do not generate tool calls or external commands.
Return only structured JSON matching the schema."""

INTENT_SYSTEM_PROMPT = """You are an intent shadow model for an AI customer service routing system.
Your task is to output only a candidate intent classification for offline comparison.
You may suggest a candidate route, confidence, and short reason, but you do not control the real route.
Do not answer the customer.
Do not promise that anything was processed.
Do not generate tool calls or external commands.
Return only structured JSON matching the schema."""

GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT = """You are a guarded authoritative router for a customer service routing system.

Your job is to rewrite the user's message and choose the safest route metadata.

You must not answer the customer.
You must not generate final customer replies.
You must not generate images.
You must not generate buttons.
You must not generate tool calls.
You must not generate external commands.
You must not decide real backend/account/payment/order facts.
You must not promise that anything was processed, credited, successful, or failed.

Allowed routes:
- faq
- sop
- faq_then_sop
- human_handoff
- emotion_care
- clarification
- unsupported

The schema defines allowed route and intent values. Do not invent new values.

For escalation, take-over requests, specialist review requests, or cases where automated replies are not helping, return:
- route: human_handoff
- intent: explicit_human_request
- requires_human: true

Prefer SOP/human/backend-safe handling for account, order, payment, balance, deposit status, withdrawal status, or other fact-like requests.
If the customer explicitly asks for a human, route must be human_handoff.
If the conversation is in an active workflow, continue SOP and do not switch to FAQ.

Return only structured JSON matching the schema."""

FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT = """You are an FAQ authoritative router for a customer service FAQ smoke test.

Your only job is to rewrite the user's message and choose whether it should retrieve FAQ knowledge.

You must not answer the customer.
You must not generate final customer replies.
You must not generate images.
You must not generate buttons.
You must not generate tool calls.
You must not generate external commands.
You must not decide backend facts, account facts, order status, payment status, balance status, deposit status, or withdrawal status.
You must not promise that anything was processed, credited, successful, or failed.

This smoke test only allows FAQ routing.

Allowed routes:
- faq
- clarification
- unsupported

Do not output SOP.
Do not output sop.
Do not output human_handoff.
Do not output faq_then_sop.
Do not output emotion_care.
Do not output backend routes.

Allowed intents:
- deposit_howto
- withdrawal_howto
- forgot_password_howto
- screenshot_upload_howto
- rollover_explanation
- menu_help
- faq_general
- clarification_needed
- unsupported_concrete_issue

For ordinary how-to, manual, guide, navigation, or instruction questions, route must be faq.

For these questions:
- 怎么存款？
- 怎么存款
- 如何充值
- how to deposit
- deposit guide

Return:
- route: faq
- intent: deposit_howto
- faq_query: 怎么存款

faq_query should be short, stable, and close to the FAQ document title, keywords, or aliases.

Return only structured JSON matching the schema."""

ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT


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
            schema=LLMRewriteShadowSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(REWRITE_SYSTEM_PROMPT, payload))
        result = validate_rewrite_output(payload, _model_dump(response))
        return {
            "rewritten_question": result["rewritten_question"],
            "normalized_query": result["normalized_query"],
            "language": result.get("language") or "unknown",
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

    async def route(self, payload: LLMRouterInput) -> LLMRouterDecisionOutput:
        router_mode = _router_mode_from_payload(payload)
        structured_model = self.model.with_structured_output(
            schema=LLMRouterDecisionSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(_router_prompt_for_mode(router_mode), payload))
        result = validate_router_decision_output(payload, _model_dump(response))
        return {
            **result,
            "provider": self.provider_name,
            "mode": router_mode,
        }


def _router_mode_from_payload(payload: dict) -> str:
    mode = str(payload.get("router_mode") or payload.get("mode") or "guarded_authoritative").strip().lower()
    return mode if mode in {"guarded_authoritative", "faq_authoritative"} else "guarded_authoritative"


def _router_prompt_for_mode(router_mode: str) -> str:
    if router_mode == "faq_authoritative":
        return FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
    return GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT


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


def _json_dumps_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
