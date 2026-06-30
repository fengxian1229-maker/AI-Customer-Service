from app.services.language_policy import (
    detect_language_deterministic,
    normalize_language_code,
    resolve_language_policy,
)


def test_normalize_language_code_accepts_legacy_aliases():
    assert normalize_language_code("zh") == "zh-Hans"
    assert normalize_language_code("cn") == "zh-Hans"
    assert normalize_language_code("zh_cn") == "zh-Hans"
    assert normalize_language_code("zh-TW") == "zh-Hant"
    assert normalize_language_code("traditional") == "zh-Hant"
    assert normalize_language_code("filipino") == "tl"
    assert normalize_language_code("burmese") == "my"
    assert normalize_language_code("malay") == "ms"
    assert normalize_language_code("thai") == "th"


def test_detect_language_deterministic_never_infers_language_from_text():
    for text, reason in [
        ("", "empty_input"),
        ("请问怎么充值，需要上传截图吗", "llm_language_required"),
        ("Please help with my deposit account", "llm_language_required"),
        ("mi depósito no llegó, usuario andy", "llm_language_required"),
        ("paki tulong po sa deposito ko", "llm_language_required"),
        ("ช่วยตรวจสอบการฝากเงินของฉัน", "llm_language_required"),
        ("ကျွန်ုပ်အကောင့်ငွေသွင်းထားပါတယ်", "llm_language_required"),
        ("tolong semak akaun deposit saya", "llm_language_required"),
    ]:
        result = detect_language_deterministic(text)

        assert result["detected_language"] == "unknown"
        assert result["language_confidence"] == 0.0
        assert result["reason"] == reason


def test_llm_router_language_is_audit_only_and_rewrite_language_updates_slot_memory():
    slot_memory = {"last_user_language": "es"}
    result = resolve_language_policy(
        {
            "raw_user_input": "deposit help",
            "slot_memory": slot_memory,
            "recent_messages": [{"sender_role": "customer", "detected_language": "zh-Hant"}],
            "rewrite_result": {"detected_language": "tl", "source": "llm_rewrite_authoritative", "language_confidence": 0.91},
            "llm_router_result": {"status": "accepted", "language": "en", "confidence": 0.91},
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "zh-Hant", "en", "es", "tl"],
    )

    assert result["detected_language"] == "tl"
    assert result["reply_language"] == "tl"
    assert result["language_source"] == "rewrite_result"
    assert result["llm_language"] == "tl"
    assert result["llm_language_source"] == "rewrite_result"
    assert slot_memory["last_user_language"] == "tl"
    assert slot_memory["last_reply_language"] == "tl"


def test_authoritative_rewrite_language_supports_tl_and_zh_hant():
    for language in ("tl", "zh-Hant"):
        result = resolve_language_policy(
            {
                "raw_user_input": "no keyword matching",
                "slot_memory": {},
                "recent_messages": [],
                "rewrite_result": {
                    "source": "llm_rewrite_authoritative",
                    "detected_language": language,
                    "language_confidence": 0.91,
                },
            },
            tenant_default_language="zh-Hans",
            supported_languages=["zh-Hans", "zh-Hant", "tl"],
        )

        assert result["reply_language"] == language
        assert result["language_source"] == "rewrite_result"


def test_authoritative_rewrite_language_is_used_when_router_has_no_supported_language():
    slot_memory = {}
    result = resolve_language_policy(
        {
            "raw_user_input": "anything",
            "slot_memory": slot_memory,
            "recent_messages": [],
            "llm_router_result": {"status": "accepted", "language": "unknown"},
            "rewrite_result": {"language": "tl", "source": "llm_rewrite_authoritative"},
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "tl"],
    )

    assert result["reply_language"] == "tl"
    assert result["language_source"] == "rewrite_result"
    assert result["llm_language"] == "tl"
    assert slot_memory["last_user_language"] == "tl"


def test_llm_language_unsupported_falls_back_to_slot_memory():
    slot_memory = {"last_user_language": "es"}
    result = resolve_language_policy(
        {
            "raw_user_input": "anything",
            "slot_memory": slot_memory,
            "recent_messages": [{"sender_role": "customer", "detected_language": "en"}],
            "llm_router_result": {"status": "accepted", "language": "ja"},
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "es"],
    )

    assert result["reply_language"] == "es"
    assert result["language_source"] == "slot_memory"
    assert slot_memory["last_user_language"] == "es"
    assert slot_memory["last_reply_language"] == "es"


def test_llm_language_unknown_falls_back_to_slot_memory():
    result = resolve_language_policy(
        {
            "raw_user_input": "anything",
            "slot_memory": {"last_user_language": "tl"},
            "recent_messages": [],
            "llm_router_result": {"status": "accepted", "language": "unknown"},
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "tl"],
    )

    assert result["reply_language"] == "tl"
    assert result["language_source"] == "slot_memory"


def test_no_llm_or_history_uses_tenant_default_language():
    result = resolve_language_policy(
        {"raw_user_input": "Please help with my withdrawal", "slot_memory": {}, "recent_messages": []},
        tenant_default_language="en",
        supported_languages=["zh-Hans", "en", "tl"],
    )

    assert result["detected_language"] == "unknown"
    assert result["reply_language"] == "en"
    assert result["language_source"] == "tenant_default"


def test_file_received_without_text_uses_slot_memory_language_without_overwriting_user_language():
    slot_memory = {"last_user_language": "tl"}
    result = resolve_language_policy(
        {
            "event_type": "FILE_RECEIVED",
            "raw_user_input": "",
            "slot_memory": slot_memory,
            "recent_messages": [],
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "en", "tl"],
    )

    assert result["detected_language"] == "unknown"
    assert result["reply_language"] == "tl"
    assert slot_memory["last_user_language"] == "tl"
    assert slot_memory["last_reply_language"] == "tl"


def test_recent_messages_text_content_without_language_metadata_is_ignored():
    result = resolve_language_policy(
        {
            "raw_user_input": "D123456",
            "slot_memory": {},
            "recent_messages": [{"sender_role": "customer", "text_content": "paki tulong po"}],
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "tl"],
    )

    assert result["reply_language"] == "zh-Hans"
    assert result["language_source"] == "tenant_default"


def test_recent_messages_explicit_detected_language_can_be_fallback():
    result = resolve_language_policy(
        {
            "raw_user_input": "D123456",
            "slot_memory": {},
            "recent_messages": [{"sender_role": "customer", "detected_language": "en"}],
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "en"],
    )

    assert result["reply_language"] == "en"
    assert result["language_source"] == "recent_messages"
