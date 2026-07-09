from __future__ import annotations

from typing import Any

from app.services.language_policy import normalize_language_code
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.slot_extractors import normalize_text


async def finalize_user_visible_text(
    *,
    fallback_text: str,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
    tenant_id: str = "default",
    channel_type: str = "livechat",
    conversation_id: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    raw_user_input: str | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reply_language: str | None = None,
    conversation_language: str | None = None,
    detected_language: str | None = None,
    slot_memory: dict[str, Any] | None = None,
    active_workflow: str | None = None,
    workflow_stage: str | None = None,
    status: str | None = None,
    route: str = "final_reply",
    intent: str = "fixed_user_visible_text",
    node_reply_template: str = "default_final_reply",
    reply_plan_kind: str = "fixed_user_visible_text",
    allowed_facts: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = normalize_text(fallback_text)
    if not fallback:
        return {"text": "", "state": {"final_response_text": None}}

    target_language = resolve_user_reply_language(
        reply_language=reply_language,
        conversation_language=conversation_language,
        detected_language=detected_language,
        slot_memory=slot_memory,
    )
    state = {
        "tenant_id": tenant_id or "default",
        "channel_type": channel_type or "livechat",
        "conversation_id": conversation_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "raw_user_input": raw_user_input or fallback,
        "rewritten_question": raw_user_input or fallback,
        "recent_messages": list(recent_messages or []),
        "route": route,
        "intent_result": {"intent": intent, "route": route},
        "active_workflow": active_workflow,
        "workflow_stage": workflow_stage,
        "status": status,
        "slot_memory": dict(slot_memory or {}),
        "missing_slots": [],
        "sop_action": None,
        "rag_result": None,
        "backend_result": None,
        "node_reply_template": node_reply_template,
        "node_facts": {
            "fallback_text": fallback,
            "allowed_facts": list(allowed_facts or [fallback]),
        },
        "detected_language": normalize_language_code(detected_language) if detected_language else target_language,
        "conversation_language": normalize_language_code(conversation_language) if conversation_language else target_language,
        "reply_language": target_language,
        "response_text": fallback,
        "response_text_fallback": fallback,
        "reply_plan": build_reply_plan(
            kind=reply_plan_kind,
            fallback_text=fallback,
            allowed_facts=list(allowed_facts or [fallback]),
            metadata=dict(metadata or {}),
        ),
        "commands": [],
    }
    if not llm_final_reply_enabled or not final_reply_service or not hasattr(final_reply_service, "compose"):
        return {
            "text": fallback,
            "state": {
                **state,
                "final_response_text": fallback,
                "final_reply_result": {"status": "fallback", "fallback_reason": "disabled_or_missing_provider"},
            },
        }
    try:
        composed = await final_reply_service.compose(state)
    except Exception as exc:
        return {
            "text": fallback,
            "state": {
                **state,
                "final_response_text": fallback,
                "final_reply_result": {
                    "status": "fallback",
                    "fallback_reason": "exception",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:1000],
                },
            },
        }
    final_text = normalize_text((composed or {}).get("final_response_text")) or fallback
    return {"text": final_text, "state": {**state, **dict(composed or {}), "final_response_text": final_text}}


def resolve_user_reply_language(
    *,
    reply_language: str | None = None,
    conversation_language: str | None = None,
    detected_language: str | None = None,
    slot_memory: dict[str, Any] | None = None,
    tenant_default_language: str = "es",
) -> str:
    memory = slot_memory or {}
    candidates = [
        reply_language,
        memory.get("last_reply_language"),
        conversation_language,
        detected_language,
        tenant_default_language,
    ]
    for candidate in candidates:
        normalized = normalize_language_code(candidate)
        if normalized != "unknown":
            return normalized
    return "es"
