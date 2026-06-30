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


def test_detect_language_deterministic_supported_languages():
    cases = [
        ("请问怎么充值，需要上传截图吗", "zh-Hans"),
        ("請問怎麼儲值，需要上傳截圖嗎", "zh-Hant"),
        ("Please help with my deposit account", "en"),
        ("mi depósito no llegó, usuario andy", "es"),
        ("paki tulong po sa deposito ko", "tl"),
        ("ช่วยตรวจสอบการฝากเงินของฉัน", "th"),
        ("ကျွန်ုပ်အကောင့်ငွေသွင်းထားပါတယ်", "my"),
        ("tolong semak akaun deposit saya", "ms"),
    ]

    for text, expected in cases:
        result = detect_language_deterministic(text)
        assert result["detected_language"] == expected
        assert result["language_confidence"] >= 0.7


def test_detect_language_deterministic_returns_unknown_for_non_language_tokens():
    for text in ("", "D123456", "5000", "🙂🙂", "ok", "?"):
        result = detect_language_deterministic(text)

        assert result["detected_language"] == "unknown"
        assert result["language_confidence"] == 0.0


def test_file_received_without_text_uses_slot_memory_language_for_reply_without_overwriting_user_language():
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
    assert result["conversation_language"] == "tl"
    assert result["reply_language"] == "tl"
    assert slot_memory["last_user_language"] == "tl"
    assert slot_memory["last_reply_language"] == "tl"


def test_unknown_current_message_uses_tenant_default_when_no_history():
    result = resolve_language_policy(
        {"raw_user_input": "D123456", "slot_memory": {}, "recent_messages": []},
        tenant_default_language="en",
        supported_languages=["zh-Hans", "en", "tl"],
    )

    assert result["detected_language"] == "unknown"
    assert result["reply_language"] == "en"
    assert result["language_source"] == "tenant_default"


def test_current_language_switch_updates_reply_language_and_slot_memory():
    slot_memory = {"last_user_language": "es"}
    result = resolve_language_policy(
        {
            "raw_user_input": "Please help with my withdrawal",
            "slot_memory": slot_memory,
            "recent_messages": [{"sender_role": "customer", "text_content": "mi deposito no llegó"}],
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "en", "es"],
    )

    assert result["detected_language"] == "en"
    assert result["reply_language"] == "en"
    assert result["language_source"] == "deterministic"
    assert slot_memory["last_user_language"] == "en"
    assert slot_memory["last_reply_language"] == "en"


def test_recent_messages_supply_conversation_language_when_current_unknown():
    result = resolve_language_policy(
        {
            "raw_user_input": "D123456",
            "slot_memory": {},
            "recent_messages": [{"sender_role": "customer", "text_content": "paki tulong po"}],
        },
        tenant_default_language="zh-Hans",
        supported_languages=["zh-Hans", "tl"],
    )

    assert result["detected_language"] == "unknown"
    assert result["conversation_language"] == "tl"
    assert result["reply_language"] == "tl"
    assert result["language_source"] == "recent_messages"
