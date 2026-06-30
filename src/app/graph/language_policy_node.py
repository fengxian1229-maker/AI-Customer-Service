from __future__ import annotations

from typing import Any

from app.graph.state import GraphState
from app.services.language_policy import parse_supported_languages, resolve_language_policy


def make_language_policy_node(**settings):
    async def node(state: GraphState) -> GraphState:
        return language_policy_node(state, **settings)

    return node


def language_policy_node(
    state: GraphState,
    *,
    language_detection_enabled: bool = True,
    language_detection_min_confidence: float = 0.70,
    tenant_persona_default_language: str = "zh-Hans",
    tenant_supported_languages: str | list[str] = "zh-Hans,zh-Hant,en,es,tl,th,my,ms",
    language_fallback: str = "zh-Hans",
    language_persist_to_slot_memory: bool = True,
) -> GraphState:
    if not language_detection_enabled:
        supported = parse_supported_languages(tenant_supported_languages)
        reply_language = tenant_persona_default_language if tenant_persona_default_language in supported else supported[0]
        slot_memory = dict(state.get("slot_memory") or {})
        if language_persist_to_slot_memory:
            slot_memory["last_reply_language"] = reply_language
        policy = {
            "detected_language": "unknown",
            "language_confidence": 0.0,
            "deterministic_language": "unknown",
            "deterministic_language_confidence": 0.0,
            "llm_language": "unknown",
            "llm_language_source": None,
            "llm_language_confidence": 0.0,
            "llm_language_accepted": False,
            "language_source": "tenant_default",
            "conversation_language": reply_language,
            "reply_language": reply_language,
            "supported_languages": supported,
            "reason": "language detection disabled",
            "detection_reason": "disabled",
        }
        return _with_language_state(state, slot_memory, policy)

    previous_slot_memory = dict(state.get("slot_memory") or {})
    state_for_policy = {**state, "slot_memory": dict(previous_slot_memory)}
    policy = resolve_language_policy(
        state_for_policy,
        tenant_default_language=tenant_persona_default_language,
        supported_languages=tenant_supported_languages,
        min_confidence=language_detection_min_confidence,
        fallback_language=language_fallback,
        persist_to_slot_memory=language_persist_to_slot_memory,
    )
    accepted = policy.get("language_source") == "rewrite_result"
    policy["llm_language_accepted"] = accepted
    slot_memory = dict(state_for_policy.get("slot_memory") or {})
    if language_persist_to_slot_memory and not accepted:
        if "last_user_language" in previous_slot_memory:
            slot_memory["last_user_language"] = previous_slot_memory["last_user_language"]
        else:
            slot_memory.pop("last_user_language", None)
    return _with_language_state(state, slot_memory, policy)


def _with_language_state(state: GraphState, slot_memory: dict[str, Any], policy: dict[str, Any]) -> GraphState:
    return {
        **state,
        "slot_memory": slot_memory,
        "detected_language": policy.get("detected_language"),
        "language_confidence": policy.get("language_confidence"),
        "language_source": policy.get("language_source"),
        "conversation_language": policy.get("conversation_language"),
        "reply_language": policy.get("reply_language"),
        "supported_languages": list(policy.get("supported_languages") or []),
        "language_result": policy,
    }
