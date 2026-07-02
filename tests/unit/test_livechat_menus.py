from app.services.livechat_menus import build_quick_replies_event, detect_button_id, fallback_text, get_menu


def test_get_menu_returns_language_specific_buttons_and_fallback():
    zh = get_menu("main", "zh-Hans")
    fallback = get_menu("main", "tl")

    assert zh["title"].startswith("你好")
    assert [button["id"] for button in zh["buttons"]] == [
        "deposit_menu",
        "withdrawal_menu",
        "main_pending_reply",
        "other_menu",
    ]
    assert fallback["language"] == "es"


def test_build_quick_replies_event_matches_livechat_shape():
    menu = get_menu("deposit", "en")
    event = build_quick_replies_event(menu)

    assert event["type"] == "rich_message"
    assert event["template_id"] == "quick_replies"
    assert event["elements"][0]["title"] == "Choose the deposit issue:"
    assert event["elements"][0]["buttons"][0] == {
        "type": "message",
        "text": "🧾 Deposit not credited",
        "value": "🧾 Deposit not credited",
        "postback_id": "main_deposito",
        "user_ids": [],
    }


def test_detect_button_id_supports_labels_numbers_and_aliases():
    assert detect_button_id("1", "deposit", "es") == "main_deposito"
    assert detect_button_id("🧾 Depósito no acreditado", "deposit", "es") == "main_deposito"
    assert detect_button_id("deposit", "main", "en") == "deposit_menu"
    assert detect_button_id("真人客服", "main", "zh") == "global_human"


def test_fallback_text_renders_numbered_menu():
    text = fallback_text(get_menu("other", "en"))

    assert text.startswith("Choose the support type:")
    assert "1. 🔑 Forgot password" in text


def test_recovery_menus_are_available():
    assert get_menu("main_recovery", "en")["buttons"][0]["id"] == "route_previous"
    assert get_menu("deposit_recovery", "en")["buttons"][1]["id"] == "route_main"
