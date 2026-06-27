import pytest

from app.services.knowledge_blocks import (
    default_text_answer_blocks,
    normalize_metadata_json,
    normalize_question_aliases,
    validate_answer_blocks,
)


def test_normalize_question_aliases_dedupes_and_preserves_order():
    assert normalize_question_aliases([" how to deposit ", "", "如何充值", "how to deposit", None]) == [
        "how to deposit",
        "如何充值",
    ]


def test_normalize_metadata_json_accepts_none_and_dict_only():
    assert normalize_metadata_json(None) == {}
    assert normalize_metadata_json({"intent_id": "deposit_howto"}) == {"intent_id": "deposit_howto"}
    with pytest.raises(ValueError, match="metadata_json must be a dict"):
        normalize_metadata_json(["bad"])


def test_validate_answer_blocks_accepts_text_image_and_buttons():
    blocks = validate_answer_blocks(
        [
            {"type": "text", "text": "按页面提示完成充值。"},
            {
                "type": "image",
                "asset_key": "deposit_howto",
                "caption": "",
                "position": "before",
                "platform_asset_map": {"default": "bot66tornado/assets/tutorials/JUE999/deposit.jpg"},
            },
            {"type": "buttons", "menu_key": "deposit_recovery"},
        ]
    )

    assert [block["type"] for block in blocks] == ["text", "image", "buttons"]


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([{"type": "video", "url": "x"}], "unknown answer block type"),
        ([{"type": "text", "text": " "}], "text block requires non-empty text"),
        ([{"type": "image"}], "image block requires asset_key"),
        ([{"type": "buttons"}], "buttons block requires menu_key"),
        ({"type": "text", "text": "bad"}, "answer_blocks must be a list"),
        (["bad"], "answer block must be a dict"),
    ],
)
def test_validate_answer_blocks_rejects_invalid_blocks(blocks, message):
    with pytest.raises(ValueError, match=message):
        validate_answer_blocks(blocks)


def test_default_text_answer_blocks_uses_content_fallback():
    assert default_text_answer_blocks("  上传截图说明  ") == [{"type": "text", "text": "上传截图说明"}]
    assert default_text_answer_blocks("") == []
