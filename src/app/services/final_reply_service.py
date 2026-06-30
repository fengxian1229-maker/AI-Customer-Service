from __future__ import annotations

from typing import Any

from app.services.language_policy import normalize_language_code, parse_supported_languages
from app.workflows.final_reply_policy import accepted_result, fallback_result, validate_final_reply_output
from app.workflows.slot_extractors import normalize_text


class FinalReplyService:
    def __init__(
        self,
        provider=None,
        *,
        enabled: bool = False,
        min_confidence: float = 0.70,
        fallback_enabled: bool = True,
        tenant_persona: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.enabled = bool(enabled)
        self.min_confidence = float(min_confidence)
        self.fallback_enabled = bool(fallback_enabled)
        self.tenant_persona = {
            "default_language": "zh-Hans",
            "tone": "polite",
            "assistant_name": None,
            "brand_name": None,
            **dict(tenant_persona or {}),
        }
        self.tenant_persona["default_language"] = normalize_language_code(self.tenant_persona.get("default_language"))
        self.supported_languages = parse_supported_languages(self.tenant_persona.get("supported_languages"))

    async def compose(self, state: dict[str, Any]) -> dict[str, Any]:
        fallback_text = normalize_text(state.get("response_text_fallback") or state.get("response_text"))
        if not fallback_text:
            return {
                **state,
                "final_response_text": None,
                "final_reply_result": fallback_result("empty_fallback"),
            }
        if not self.enabled:
            return self._fallback_state(state, fallback_text, "disabled")
        if not self.provider or not hasattr(self.provider, "compose_final_reply"):
            return self._fallback_state(state, fallback_text, "missing_provider")

        payload = self._build_payload(state, fallback_text)
        try:
            output = await self.provider.compose_final_reply(payload)
        except Exception as exc:
            return self._fallback_state(state, fallback_text, "exception", error=exc)

        confidence = float((output or {}).get("confidence") or 0.0)
        if confidence < self.min_confidence:
            return self._fallback_state(state, fallback_text, "low_confidence")

        violations = validate_final_reply_output(state, output or {})
        if violations:
            return self._fallback_state(state, fallback_text, "guardrail_failed", violations=violations)

        text = normalize_text((output or {}).get("text"))
        return {
            **state,
            "response_text_fallback": fallback_text,
            "final_response_text": text,
            "final_reply_result": accepted_result(output or {}),
        }

    def _fallback_state(
        self,
        state: dict[str, Any],
        fallback_text: str,
        reason: str,
        *,
        violations: list[str] | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        final_text = fallback_text if self.fallback_enabled else None
        return {
            **state,
            "response_text_fallback": fallback_text,
            "final_response_text": final_text,
            "final_reply_result": fallback_result(reason, violations=violations, error=error),
        }

    def _build_payload(self, state: dict[str, Any], fallback_text: str) -> dict[str, Any]:
        return {
            "tenant_id": state.get("tenant_id"),
            "channel_type": state.get("channel_type"),
            "conversation_id": state.get("conversation_id"),
            "raw_user_input": state.get("raw_user_input"),
            "rewritten_question": state.get("rewritten_question"),
            "recent_messages": list(state.get("recent_messages") or []),
            "route": state.get("route"),
            "intent_result": state.get("intent_result"),
            "active_workflow": state.get("active_workflow"),
            "workflow_stage": state.get("workflow_stage"),
            "status": state.get("status"),
            "slot_memory": dict(state.get("slot_memory") or {}),
            "missing_slots": list(state.get("missing_slots") or []),
            "sop_action": state.get("sop_action"),
            "rag_result": state.get("rag_result"),
            "detected_language": state.get("detected_language"),
            "language_confidence": state.get("language_confidence"),
            "language_source": state.get("language_source"),
            "conversation_language": state.get("conversation_language"),
            "reply_language": state.get("reply_language"),
            "language_result": state.get("language_result"),
            "supported_languages": list(state.get("supported_languages") or self.supported_languages),
            "response_text_fallback": fallback_text,
            "reply_plan": dict(state.get("reply_plan") or {}),
            "tenant_persona": dict(self.tenant_persona),
        }
