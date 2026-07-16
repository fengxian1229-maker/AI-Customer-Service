import json
from pathlib import Path

import pytest

from app.services.faq_renderer import render_answer_blocks_preview


def test_renderer_preview_renders_text_block():
    preview = render_answer_blocks_preview([{"type": "text", "text": "  按页面提示完成充值。  "}])

    assert preview == [{"kind": "text", "text": "按页面提示完成充值。"}]


def test_renderer_preview_selects_platform_asset_ref():
    preview = render_answer_blocks_preview(
        [
            {
                "type": "image",
                "asset_key": "deposit_howto",
                "caption": "充值教程",
                "position": "before",
                "platform_asset_map": {
                    "JUE999": "data/assets/customer-service/tutorials/JUE999/deposit.jpg",
                    "default": "data/assets/customer-service/tutorials/default/deposit.jpg",
                },
            }
        ],
        platform="jue999",
        channel_type="livechat",
        language="zh",
    )

    assert preview == [
        {
            "kind": "image",
            "asset_key": "deposit_howto",
            "asset_ref": "data/assets/customer-service/tutorials/JUE999/deposit.jpg",
            "caption": "充值教程",
            "position": "before",
        }
    ]


def test_renderer_preview_defaults_to_con777_platform_asset_ref():
    preview = render_answer_blocks_preview(
        [
            {
                "type": "image",
                "asset_key": "deposit_howto",
                "platform_asset_map": {
                    "CON777": "data/assets/customer-service/tutorials/CON777/deposit.jpg",
                },
            }
        ]
    )

    assert preview[0]["asset_ref"] == "data/assets/customer-service/tutorials/CON777/deposit.jpg"


def test_renderer_preview_falls_back_to_default_asset_ref():
    preview = render_answer_blocks_preview(
        [
            {
                "type": "image",
                "asset_key": "withdrawal_howto",
                "position": "before",
                "platform_asset_map": {
                    "default": "data/assets/customer-service/tutorials/default/withdrawal.jpg",
                },
            }
        ],
        platform="MXN",
    )

    assert preview[0]["asset_ref"] == "data/assets/customer-service/tutorials/default/withdrawal.jpg"


def test_renderer_preview_allows_missing_asset_ref_when_no_platform_or_default_match():
    preview = render_answer_blocks_preview(
        [
            {
                "type": "image",
                "asset_key": "forgot_password",
                "platform_asset_map": {"JUE999": "data/assets/customer-service/tutorials/JUE999/forgot-password.jpg"},
            }
        ],
        platform="MXN",
    )

    assert preview[0]["asset_ref"] is None


def test_renderer_preview_renders_buttons_block_without_expanding_buttons():
    preview = render_answer_blocks_preview([{"type": "buttons", "menu_key": "deposit_recovery"}])

    assert preview == [{"kind": "buttons", "menu_key": "deposit_recovery"}]


def test_renderer_preview_preserves_block_order():
    preview = render_answer_blocks_preview(
        [
            {"type": "image", "asset_key": "deposit_howto", "position": "before"},
            {"type": "text", "text": "充值说明"},
            {"type": "buttons", "menu_key": "deposit_recovery"},
        ]
    )

    assert [block["kind"] for block in preview] == ["image", "text", "buttons"]
    assert preview[0]["position"] == "before"


def test_renderer_preview_uses_default_multimodal_seed_deposit_howto():
    seed_path = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "default_multimodal_faq_seed.json"
    seed_rows = json.loads(seed_path.read_text(encoding="utf-8"))
    deposit = next(row for row in seed_rows if row["metadata_json"]["intent_id"] == "deposit_howto")

    preview = render_answer_blocks_preview(deposit["answer_blocks"])

    assert preview[0]["kind"] == "image"
    assert preview[0]["asset_key"] == "deposit_howto"
    assert preview[0]["asset_ref"] == "data/assets/customer-service/tutorials/CON777/deposit.jpg"
    assert preview[1]["kind"] == "text"
    assert len(preview) == 2


def test_renderer_preview_uses_default_multimodal_seed_platform_asset_ref():
    seed_path = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "default_multimodal_faq_seed.json"
    seed_rows = json.loads(seed_path.read_text(encoding="utf-8"))
    deposit = next(row for row in seed_rows if row["metadata_json"]["intent_id"] == "deposit_howto")

    preview = render_answer_blocks_preview(deposit["answer_blocks"], platform="PAG99")

    assert preview[0]["asset_ref"] == "data/assets/customer-service/tutorials/PAG99/deposit.jpg"


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([{"type": "text", "text": " "}], "text block requires non-empty text"),
        ([{"type": "image"}], "image block requires asset_key"),
        ([{"type": "image", "asset_key": "deposit_howto", "platform_asset_map": ["bad"]}], "platform_asset_map must be a dict"),
    ],
)
def test_renderer_preview_rejects_invalid_blocks(blocks, message):
    with pytest.raises(ValueError, match=message):
        render_answer_blocks_preview(blocks)


def test_renderer_preview_module_stays_side_effect_free():
    import app.services.faq_renderer as faq_renderer

    module_path = Path(faq_renderer.__file__).read_text(encoding="utf-8")

    assert "sender_worker" not in module_path
    assert "outbound_messages" not in module_path
