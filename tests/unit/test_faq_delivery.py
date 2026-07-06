from app.services.faq_delivery import prepare_faq_context_for_delivery, strip_menu_directives


def test_strip_menu_directives_removes_menu_only_sentences():
    text = (
        "若你要進行充值，請依照以下步驟操作。\n\n"
        "請先實際操作一次。若你已經完成付款，但遊戲帳號仍未到帳，請回到選單並選擇「存款未到帳」。"
    )

    cleaned = strip_menu_directives(text)

    assert "若你要進行充值" in cleaned
    assert "請先實際操作一次" in cleaned
    assert "回到選單" not in cleaned
    assert "存款未到帳" not in cleaned


def test_prepare_faq_context_for_delivery_strips_text_blocks_and_preserves_media():
    context = {
        "answer": "請回到選單並選擇「提款未到帳」。",
        "answer_blocks": [
            {"type": "image", "asset_key": "withdrawal_howto", "position": "before"},
            {
                "type": "text",
                "text": "提款教程。請回到選單並選擇「提款未到帳」。如果沒有出現金額欄位或頁面不讓你繼續，請選擇「無法提款」。",
            },
        ],
    }

    prepared = prepare_faq_context_for_delivery(context, {"intent_result": {"intent": "withdrawal_howto"}})

    assert prepared["answer"] == ""
    assert prepared["answer_blocks"][0] == {"type": "image", "asset_key": "withdrawal_howto", "position": "before"}
    assert prepared["answer_blocks"][1]["text"] == "提款教程。"
    assert context["answer_blocks"][1]["text"].endswith("請選擇「無法提款」。")


def test_prepare_faq_context_for_delivery_strips_document_content():
    context = {
        "documents": [
            {
                "id": 1,
                "title": "充值教程",
                "content": "充值教程。請回到選單並選擇「存款未到帳」。",
            }
        ]
    }

    prepared = prepare_faq_context_for_delivery(context, {"intent_result": {"intent": "deposit_howto"}})

    assert prepared["documents"][0]["content"] == "充值教程。"
    assert context["documents"][0]["content"] == "充值教程。請回到選單並選擇「存款未到帳」。"


def test_prepare_faq_context_for_delivery_preserves_livechat_button_text():
    context = {
        "answer": "請回到選單並選擇「存款未到帳」。",
        "answer_blocks": [{"type": "text", "text": "請回到選單並選擇「存款未到帳」。"}],
    }

    prepared = prepare_faq_context_for_delivery(
        context,
        {"intent_result": {"intent": "deposit_howto", "faq_trigger_source": "livechat_button"}},
    )

    assert prepared is context
    assert prepared["answer_blocks"][0]["text"] == "請回到選單並選擇「存款未到帳」。"
