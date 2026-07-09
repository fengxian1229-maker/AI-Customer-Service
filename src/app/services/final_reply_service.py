from __future__ import annotations

import re
from typing import Any

from app.services.chinese_script import adapt_chinese_script
from app.services.language_policy import normalize_language_code, parse_supported_languages
from app.services.reply_renderer import render_customer_reply
from app.workflows.final_reply_policy import (
    accepted_result,
    accepted_with_warnings_result,
    fallback_result,
    validate_final_reply_output,
)
from app.workflows.final_reply_templates import (
    build_node_facts,
    build_node_reply_instruction,
    resolve_node_reply_template_id,
)
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
            "default_language": "es",
            "tone": "polite",
            "assistant_name": None,
            "brand_name": None,
            **dict(tenant_persona or {}),
        }
        self.tenant_persona["default_language"] = normalize_language_code(self.tenant_persona.get("default_language"))
        self.supported_languages = parse_supported_languages(self.tenant_persona.get("supported_languages"))

    async def compose(self, state: dict[str, Any]) -> dict[str, Any]:
        reply_language = self._target_reply_language(state)
        fallback_text_raw = normalize_text(
            state.get("response_text_fallback")
            or state.get("response_text")
            or self._render_structured_fallback(state)
        )
        fallback_text = normalize_text(_sanitize_customer_visible_internal_labels(adapt_chinese_script(fallback_text_raw, reply_language)))
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

        payload_fallback_text = normalize_text(_sanitize_customer_visible_internal_labels(fallback_text_raw or fallback_text))
        payload = self._build_payload(state, payload_fallback_text)
        try:
            output = await self.provider.compose_final_reply(payload)
        except Exception as exc:
            return self._fallback_state(state, fallback_text, "exception", error=exc)

        confidence = float((output or {}).get("confidence") or 0.0)
        text = normalize_text((output or {}).get("text"))
        if not text:
            return self._fallback_state(state, fallback_text, "empty_model_text")
        violations = validate_final_reply_output(state, output or {})
        warning_reason = None
        if confidence < self.min_confidence:
            warning_reason = "low_confidence"
            violations = sorted(set([*violations, "low_confidence"]))
        if violations:
            return {
                **state,
                "response_text_fallback": fallback_text,
                "final_response_text": text,
                "final_reply_result": accepted_with_warnings_result(
                    output or {},
                    violations=violations,
                    warning_reason=warning_reason or "guardrail_audit",
                ),
            }

        return {
            **state,
            "response_text_fallback": fallback_text,
            "final_response_text": text,
            "final_reply_result": accepted_result(output or {}),
        }

    def _render_structured_fallback(self, state: dict[str, Any]) -> str | None:
        customer_reply = state.get("customer_reply") if isinstance(state.get("customer_reply"), dict) else {}
        reply_intent = customer_reply.get("intent")
        if not reply_intent:
            return None
        facts = customer_reply.get("facts") if isinstance(customer_reply.get("facts"), dict) else {}
        return render_customer_reply(
            reply_intent,
            facts=facts,
            reply_language=state.get("reply_language") or customer_reply.get("language"),
            tenant_persona=self.tenant_persona,
        )

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
        node_reply_template = resolve_node_reply_template_id(state)
        reply_language = self._target_reply_language(state)
        node_facts = build_node_facts(state)
        reply_plan = dict(state.get("reply_plan") or {})
        rag_result = state.get("rag_result")
        backend_result = state.get("backend_result")
        return {
            "tenant_id": state.get("tenant_id"),
            "channel_type": state.get("channel_type"),
            "conversation_id": state.get("conversation_id"),
            "raw_user_input": state.get("raw_user_input"),
            "rewritten_question": state.get("rewritten_question"),
            "recent_messages": list(state.get("recent_messages") or []),
            "previous_thread_memory": list(state.get("previous_thread_memory") or []),
            "route": state.get("route"),
            "intent_result": state.get("intent_result"),
            "active_workflow": state.get("active_workflow"),
            "workflow_stage": state.get("workflow_stage"),
            "status": state.get("status"),
            "slot_memory": dict(state.get("slot_memory") or {}),
            "missing_slots": list(state.get("missing_slots") or []),
            "sop_action": state.get("sop_action"),
            "rag_result": rag_result,
            "backend_result": backend_result,
            "node_reply_template": node_reply_template,
            "node_reply_instruction": build_node_reply_instruction(node_reply_template),
            "node_facts": node_facts,
            "detected_language": state.get("detected_language"),
            "language_confidence": state.get("language_confidence"),
            "language_source": state.get("language_source"),
            "conversation_language": state.get("conversation_language"),
            "reply_language": state.get("reply_language"),
            "language_result": state.get("language_result"),
            "supported_languages": list(state.get("supported_languages") or self.supported_languages),
            "response_text_fallback": fallback_text,
            "reply_plan": reply_plan,
            "commands": list(state.get("commands") or []),
            "tenant_persona": dict(self.tenant_persona),
        }

    def _target_reply_language(self, state: dict[str, Any]) -> str:
        language = normalize_language_code(state.get("reply_language"))
        if language != "unknown":
            return language
        return normalize_language_code(self.tenant_persona.get("default_language"))


def _sanitize_customer_visible_internal_labels(text: str) -> str:
    replacements = (
        ("后台回复显示", "查询结果显示"),
        ("后台工作人员回复显示", "查询结果显示"),
        ("后台人员回复显示", "查询结果显示"),
        ("後台回覆顯示", "查詢結果顯示"),
        ("後台工作人員回覆顯示", "查詢結果顯示"),
        ("後台人員回覆顯示", "查詢結果顯示"),
        ("后台核实时", "为您核实时"),
        ("後台核實時", "為您核實時"),
        ("后台已进行回复", "已收到处理更新"),
        ("後台已進行回覆", "已收到處理更新"),
        ("后台已回复", "已收到处理更新"),
        ("後台已回覆", "已收到處理更新"),
        ("后台回复", "已为您核实到"),
        ("後台回覆", "已為您核實到"),
        ("后台显示", "查询结果显示"),
        ("後台顯示", "查詢結果顯示"),
        ("后台工作人员", "处理人员"),
        ("後台工作人員", "處理人員"),
        ("后台人员", "处理人员"),
        ("後台人員", "處理人員"),
        ("后台", "我们"),
        ("後台", "我們"),
    )
    sanitized = str(text or "")
    for old, new in replacements:
        sanitized = sanitized.replace(old, new)
    sanitized = re.sub(r"\bbackend\s+replied\b", "we received an update", sanitized, flags=re.I)
    sanitized = re.sub(r"\bbackend\s+shows\b", "the check result shows", sanitized, flags=re.I)
    sanitized = re.sub(r"\bbackend\b", "support team", sanitized, flags=re.I)
    return sanitized
