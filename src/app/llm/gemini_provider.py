import json

from app.llm.contracts import (
    LLMIntentShadowInput,
    LLMIntentShadowOutput,
    LLMIntentShadowSchema,
    LLMRewriteShadowInput,
    LLMRewriteShadowOutput,
    LLMRewriteShadowSchema,
)
from app.llm.guardrails import validate_intent_output, validate_rewrite_output
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


def _model_dump(response) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    raise TypeError("Gemini structured output must be a dict-like schema result.")


def _build_chat_messages(system_prompt: str, payload: dict) -> list[tuple[str, str]]:
    return [
        ("system", system_prompt),
        ("human", json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    ]
