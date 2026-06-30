from __future__ import annotations

from typing import Any

SUPPORTED_LANGUAGE_CODES = ("zh-Hans", "zh-Hant", "en", "es", "tl", "th", "my", "ms", "unknown")
FINAL_REPLY_LANGUAGE_CODES = tuple(code for code in SUPPORTED_LANGUAGE_CODES if code != "unknown")

LANGUAGE_ALIASES = {
    "zh": "zh-Hans",
    "cn": "zh-Hans",
    "zh_cn": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh_hans": "zh-Hans",
    "simplified": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh_tw": "zh-Hant",
    "tw": "zh-Hant",
    "traditional": "zh-Hant",
    "zh_hant": "zh-Hant",
    "tagalog": "tl",
    "fil": "tl",
    "filipino": "tl",
    "burmese": "my",
    "mm": "my",
    "myanmar": "my",
    "malay": "ms",
    "thai": "th",
}


def normalize_language_code(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    if raw in SUPPORTED_LANGUAGE_CODES:
        return raw
    key = raw.lower().replace(" ", "_")
    return LANGUAGE_ALIASES.get(key, raw if raw in SUPPORTED_LANGUAGE_CODES else "unknown")


def parse_supported_languages(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        items = FINAL_REPLY_LANGUAGE_CODES
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        items = list(value)
    normalized = []
    for item in items:
        code = normalize_language_code(item)
        if code != "unknown" and code not in normalized:
            normalized.append(code)
    return normalized or ["zh-Hans"]


def detect_language_deterministic(text: str | None) -> dict[str, Any]:
    value = str(text or "").strip()
    return _detected("unknown", 0.0, "llm_language_required" if value else "empty_input")


def infer_recent_user_language(recent_messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(recent_messages or []):
        if str(message.get("sender_role") or message.get("role") or "").lower() not in {"customer", "user", "external"}:
            continue
        language = normalize_language_code(message.get("language") or message.get("detected_language") or message.get("reply_language"))
        if language != "unknown":
            return {
                "detected_language": language,
                "language_confidence": 1.0,
                "language_source": "recent_messages",
                "reason": "recent message supplied explicit language metadata",
            }
    return _detected("unknown", 0.0, "no_recent_language")


def resolve_language_policy(
    state: dict[str, Any],
    tenant_default_language: str,
    supported_languages: list[str] | str | None,
    *,
    min_confidence: float = 0.70,
    fallback_language: str = "zh-Hans",
    persist_to_slot_memory: bool = True,
) -> dict[str, Any]:
    supported = parse_supported_languages(supported_languages)
    tenant_default = _ensure_supported(normalize_language_code(tenant_default_language), supported, fallback_language)
    fallback = _ensure_supported(normalize_language_code(fallback_language), supported, tenant_default)
    slot_memory = state.setdefault("slot_memory", {})

    deterministic = detect_language_deterministic(state.get("raw_user_input"))
    llm_language, llm_source, llm_reason, llm_confidence = _rewrite_language(state, supported)
    detected_language = llm_language or "unknown"
    detected_confidence = llm_confidence if llm_language else 0.0
    if llm_language and llm_confidence >= float(min_confidence):
        conversation_language = llm_language
        source = llm_source
        reason = llm_reason
        if persist_to_slot_memory:
            slot_memory["last_user_language"] = llm_language
    else:
        source = "fallback"
        conversation_language = fallback
        reason = "fallback language"
        recent = infer_recent_user_language(list(state.get("recent_messages") or []))
        recent_language = normalize_language_code(recent.get("detected_language"))
        if recent_language != "unknown" and recent_language in supported:
            conversation_language = recent_language
            source = "recent_messages"
            reason = "recent message explicit language metadata"
        else:
            slot_language = normalize_language_code(slot_memory.get("last_user_language") or slot_memory.get("last_reply_language"))
            if slot_language != "unknown" and slot_language in supported:
                conversation_language = slot_language
                source = "slot_memory"
                reason = "slot memory supplied conversation language"
            else:
                conversation_language = tenant_default
                source = "tenant_default"
                reason = "tenant default language"

    reply_language = _ensure_supported(conversation_language, supported, fallback)
    if persist_to_slot_memory:
        if llm_language:
            slot_memory["last_user_language"] = llm_language
        slot_memory["last_reply_language"] = reply_language

    return {
        "detected_language": detected_language,
        "language_confidence": detected_confidence,
        "deterministic_language": deterministic.get("detected_language"),
        "deterministic_language_confidence": deterministic.get("language_confidence"),
        "llm_language": llm_language or "unknown",
        "llm_language_source": llm_source,
        "llm_language_confidence": llm_confidence,
        "language_source": source,
        "conversation_language": conversation_language,
        "reply_language": reply_language,
        "supported_languages": supported,
        "reason": reason,
        "detection_reason": deterministic.get("reason"),
    }


def _rewrite_language(state: dict[str, Any], supported: list[str]) -> tuple[str | None, str | None, str, float]:
    rewrite = state.get("rewrite_result") or {}
    rewrite_language = normalize_language_code(rewrite.get("detected_language") or rewrite.get("language"))
    rewrite_source = str(rewrite.get("source") or rewrite.get("language_source") or "")
    authoritative_sources = {
        "llm_guarded_authoritative",
        "llm_guarded_authoritative_post_guard",
        "llm_rewrite_authoritative",
        "llm_rewrite",
    }
    if rewrite_language != "unknown" and rewrite_language in supported and rewrite_source in authoritative_sources:
        return rewrite_language, "rewrite_result", "authoritative rewrite result supplied language", float(rewrite.get("language_confidence") or rewrite.get("confidence") or 1.0)
    return None, None, "", 0.0


def _ensure_supported(language: str, supported: list[str], fallback: str) -> str:
    normalized = normalize_language_code(language)
    if normalized != "unknown" and normalized in supported:
        return normalized
    fallback_normalized = normalize_language_code(fallback)
    if fallback_normalized != "unknown" and fallback_normalized in supported:
        return fallback_normalized
    return supported[0] if supported else "zh-Hans"


def _detected(language: str, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "detected_language": language,
        "language_confidence": confidence,
        "language_source": "deterministic",
        "reason": reason,
    }
