from app.services.livechat_menus import build_quick_replies_event, detect_button_id, fallback_text, get_menu


def test_get_menu_returns_language_specific_buttons_and_fallback():
    zh = get_menu("main", "zh-Hans")
    fallback = get_menu("main", "tl")

    assert zh["title"] == "您好，我是客服灵犀，我可以为您提供以下方面的协助：存款、提款、流水查询、上传截图，或为您转接真人客服。请告诉我您具体需要处理哪方面的问题，或者您可以点击下方的菜单按钮。"
    assert "客服灵犀" in zh["title"]
    assert "請" not in zh["title"]
    assert [button["id"] for button in zh["buttons"]] == [
        "deposit_menu",
        "withdrawal_menu",
        "main_pending_reply",
        "other_menu",
    ]
    assert zh["buttons"][0]["label"] == "💰 存款问题"
    assert fallback["language"] == "zh-Hans"


def test_main_menu_uses_long_welcome_copy_for_supported_languages():
    assert "I can help with deposits" in get_menu("main", "en")["title"]
    assert "Puedo ayudarle con depósitos" in get_menu("main", "es")["title"]
    assert "我可以為您提供以下方面的協助" in get_menu("main", "zh-Hant")["title"]


def test_get_menu_distinguishes_simplified_and_traditional_chinese():
    simplified = get_menu("deposit", "zh-Hans")
    traditional = get_menu("deposit", "zh-Hant")

    assert simplified["title"] == "请选择存款问题："
    assert simplified["buttons"][0]["label"] == "🧾 存款未到账"
    assert traditional["title"] == "請選擇存款問題："
    assert traditional["buttons"][0]["label"] == "🧾 存款未到帳"


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


def test_unknown_language_defaults_to_simplified_chinese():
    menu = get_menu("deposit", None)

    assert menu["language"] == "zh-Hans"
    assert menu["title"] == "请选择存款问题："
    assert menu["buttons"][0]["label"] == "🧾 存款未到账"
