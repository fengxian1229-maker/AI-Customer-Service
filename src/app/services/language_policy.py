from __future__ import annotations

import re
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

ZH_HANS_HINTS = ("请", "这", "吗", "账号", "注册", "充值", "提款", "截图", "后台", "处理", "确认", "转接")
ZH_HANT_HINTS = ("請", "這", "嗎", "帳號", "註冊", "儲值", "提款", "截圖", "後台", "處理", "確認", "轉接")
EN_HINTS = ("deposit", "withdraw", "account", "phone", "help", "money", "screenshot", "agent", "support", "login", "password")
ES_HINTS = (
    "depósito",
    "deposito",
    "retiro",
    "contraseña",
    "usuario",
    "dinero",
    "cuenta",
    "ayuda",
    "captura",
    "soporte",
    "no llegó",
    "no llego",
)
TL_HINTS = ("salamat", "po", "opo", "paki", "tulong", "hindi", "ako", "kayo", " ba", "saan", "deposito", "withdraw", "pera", "account")
MS_HINTS = ("akaun", "wang", "deposit", "pengeluaran", "bantuan", "tolong", "terima kasih", "tidak boleh", "saya", "anda", "gambar", "resit", "bayaran")


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
    if not value or _is_short_ambiguous(value) or _is_non_language_token(value):
        return _detected("unknown", 0.0, "empty_or_ambiguous")

    if re.search(r"[\u0e00-\u0e7f]", value):
        return _detected("th", 0.98, "thai_unicode")
    if re.search(r"[\u1000-\u109f]", value):
        return _detected("my", 0.98, "myanmar_unicode")

    lowered = value.lower()
    if _has_cjk(value):
        hant = _count_hints(value, ZH_HANT_HINTS)
        hans = _count_hints(value, ZH_HANS_HINTS)
        if hant > hans:
            return _detected("zh-Hant", 0.92, "traditional_chinese_hints")
        return _detected("zh-Hans", 0.88 if hans else 0.74, "simplified_chinese_hints" if hans else "chinese_script")

    scores = {
        "es": _count_hints(lowered, ES_HINTS) + (1 if re.search(r"[áéíóúñ¿¡]", lowered) else 0),
        "tl": _count_hints(f" {lowered} ", TL_HINTS),
        "ms": _count_hints(lowered, MS_HINTS),
        "en": _count_hints(lowered, EN_HINTS),
    }
    best, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0:
        return _detected("unknown", 0.0, "no_language_signal")
    if _ascii_word_ratio(value) < 0.45 and best in {"en", "es", "tl", "ms"}:
        return _detected("unknown", 0.0, "low_word_ratio")
    confidence = min(0.72 + (best_score * 0.08), 0.96)
    return _detected(best, confidence, f"{best}_keywords")


def infer_recent_user_language(recent_messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(recent_messages or []):
        if str(message.get("sender_role") or message.get("role") or "").lower() not in {"customer", "user", "external"}:
            continue
        text = message.get("text_content") or message.get("text") or message.get("message") or ""
        detected = detect_language_deterministic(str(text))
        if detected["detected_language"] != "unknown":
            return {
                **detected,
                "language_source": "recent_messages",
                "reason": "recent user message supplied conversation language",
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

    detected = detect_language_deterministic(state.get("raw_user_input"))
    detected_language = detected["detected_language"]
    detected_confidence = float(detected["language_confidence"])

    source = "fallback"
    conversation_language = fallback
    reason = "fallback language"

    if detected_language != "unknown" and detected_confidence >= min_confidence and detected_language in supported:
        conversation_language = detected_language
        source = "deterministic"
        reason = "current message language met confidence threshold"
        if persist_to_slot_memory:
            slot_memory["last_user_language"] = detected_language
    else:
        candidate, candidate_source, candidate_reason = _router_or_rewrite_language(state, supported)
        if candidate:
            conversation_language = candidate
            source = candidate_source
            reason = candidate_reason
        else:
            recent = infer_recent_user_language(list(state.get("recent_messages") or []))
            recent_language = normalize_language_code(recent.get("detected_language"))
            if recent_language != "unknown" and recent_language in supported and float(recent.get("language_confidence") or 0.0) >= min_confidence:
                conversation_language = recent_language
                source = "recent_messages"
                reason = "recent user message supplied conversation language"
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
        if detected_language != "unknown" and detected_confidence >= min_confidence and detected_language in supported:
            slot_memory["last_user_language"] = detected_language
        slot_memory["last_reply_language"] = reply_language

    return {
        "detected_language": detected_language,
        "language_confidence": detected_confidence,
        "language_source": source,
        "conversation_language": conversation_language,
        "reply_language": reply_language,
        "supported_languages": supported,
        "reason": reason,
        "detection_reason": detected.get("reason"),
    }


def _router_or_rewrite_language(state: dict[str, Any], supported: list[str]) -> tuple[str | None, str, str]:
    router = state.get("llm_router_result") or {}
    router_language = normalize_language_code(router.get("language"))
    if router.get("status") == "accepted" and router_language != "unknown" and router_language in supported:
        return router_language, "llm_router", "accepted router supplied language"
    rewrite = state.get("rewrite_result") or {}
    rewrite_language = normalize_language_code(rewrite.get("detected_language") or rewrite.get("language"))
    if rewrite_language != "unknown" and rewrite_language in supported:
        return rewrite_language, "rewrite_result", "rewrite result supplied language"
    return None, "", ""


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


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _count_hints(text: str, hints: tuple[str, ...]) -> int:
    return sum(1 for hint in hints if hint in text)


def _is_short_ambiguous(text: str) -> bool:
    stripped = text.strip().lower()
    return stripped in {"ok", "hi", "?", "？", ".", "。", "!"}


def _is_non_language_token(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if re.fullmatch(r"[\W_]+", stripped, flags=re.UNICODE):
        return True
    if re.fullmatch(r"[A-Z]{0,4}\d[\dA-Z._-]*", stripped, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[$¥]?\d+([.,]\d+)?", stripped):
        return True
    return False


def _ascii_word_ratio(text: str) -> float:
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return 0.0
    wordish = [char for char in chars if char.isalpha()]
    return len(wordish) / len(chars)
