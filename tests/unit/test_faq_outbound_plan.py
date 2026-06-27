import hashlib
import json
from pathlib import Path

from app.services.faq_outbound_plan import build_faq_outbound_plan, faq_plan_to_outbound_rows
from app.services.rag import BACKEND_FACT_FALLBACK_ANSWER


BASE_KWARGS = {
    "tenant_id": "default",
    "conversation_id": "livechat:chat-1",
    "inbound_event_id": "event-1",
    "platform": "JUE999",
    "channel_type": "livechat",
    "language": "zh",
}


def test_build_faq_outbound_plan_maps_text_block_to_send_text():
    plan = build_faq_outbound_plan(
        answer_blocks=[{"type": "text", "text": "按页面提示完成充值。"}],
        **BASE_KWARGS,
    )

    assert plan["source"] == "faq_answer_blocks"
    assert plan["dry_run"] is True
    assert plan["message_count"] == 1
    assert plan["messages"] == [
        {
            "block_index": 0,
            "message_kind": "text",
            "command_type": "livechat.send_text",
            "dry_run": True,
            "dedup_key": (
                "default:livechat:chat-1:event-1:faq_block:0:text:"
                f"{hashlib.sha256('按页面提示完成充值。'.encode('utf-8')).hexdigest()[:16]}"
            ),
            "payload": {"text": "按页面提示完成充值。"},
            "warnings": [],
        }
    ]


def test_build_faq_outbound_plan_maps_image_block_to_send_image():
    plan = build_faq_outbound_plan(
        answer_blocks=[
            {
                "type": "image",
                "asset_key": "deposit_howto",
                "caption": "",
                "position": "before",
                "platform_asset_map": {"JUE999": "bot66tornado/assets/tutorials/JUE999/deposit.jpg"},
            }
        ],
        **BASE_KWARGS,
    )

    message = plan["messages"][0]
    assert message["block_index"] == 0
    assert message["message_kind"] == "image"
    assert message["command_type"] == "livechat.send_image"
    assert message["dedup_key"].endswith(":image:deposit_howto")
    assert message["payload"] == {
        "asset_key": "deposit_howto",
        "asset_ref": "bot66tornado/assets/tutorials/JUE999/deposit.jpg",
        "caption": "",
        "position": "before",
    }
    assert message["warnings"] == []


def test_build_faq_outbound_plan_maps_buttons_block_to_preview_command():
    plan = build_faq_outbound_plan(
        answer_blocks=[{"type": "buttons", "menu_key": "deposit_recovery"}],
        **BASE_KWARGS,
    )

    message = plan["messages"][0]
    assert message["message_kind"] == "buttons"
    assert message["command_type"] == "livechat.buttons_preview"
    assert message["dedup_key"].endswith(":buttons:deposit_recovery")
    assert message["payload"] == {"menu_key": "deposit_recovery"}


def test_build_faq_outbound_plan_preserves_order_and_block_index():
    plan = build_faq_outbound_plan(
        answer_blocks=[
            {"type": "image", "asset_key": "deposit_howto", "position": "before"},
            {"type": "text", "text": "充值说明"},
            {"type": "buttons", "menu_key": "deposit_recovery"},
        ],
        **BASE_KWARGS,
    )

    assert [message["block_index"] for message in plan["messages"]] == [0, 1, 2]
    assert [message["message_kind"] for message in plan["messages"]] == ["image", "text", "buttons"]


def test_build_faq_outbound_plan_warns_when_image_asset_ref_missing():
    plan = build_faq_outbound_plan(
        answer_blocks=[
            {
                "type": "image",
                "asset_key": "forgot_password",
                "platform_asset_map": {"JUE999": "bot66tornado/assets/tutorials/JUE999/forgot-password.jpg"},
            }
        ],
        platform="MXN",
        tenant_id="default",
        conversation_id="livechat:chat-1",
        inbound_event_id="event-1",
        channel_type="livechat",
        language="zh",
    )

    assert plan["messages"][0]["payload"]["asset_ref"] is None
    assert plan["messages"][0]["warnings"] == ["missing_asset_ref"]


def test_build_faq_outbound_plan_text_dedup_key_uses_hash_not_full_text():
    text = "这是很长的一段 FAQ 文本，dedup key 不应该包含完整文本。"
    plan = build_faq_outbound_plan(answer_blocks=[{"type": "text", "text": text}], **BASE_KWARGS)

    dedup_key = plan["messages"][0]["dedup_key"]
    assert text not in dedup_key
    assert dedup_key.endswith(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])


def test_build_faq_outbound_plan_is_deterministic():
    answer_blocks = [
        {"type": "text", "text": "充值说明"},
        {"type": "buttons", "menu_key": "deposit_recovery"},
    ]

    assert build_faq_outbound_plan(answer_blocks=answer_blocks, **BASE_KWARGS) == build_faq_outbound_plan(
        answer_blocks=answer_blocks,
        **BASE_KWARGS,
    )


def test_build_faq_outbound_plan_uses_default_multimodal_seed_deposit_howto():
    seed_path = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "default_multimodal_faq_seed.json"
    seed_rows = json.loads(seed_path.read_text(encoding="utf-8"))
    deposit = next(row for row in seed_rows if row["metadata_json"]["intent_id"] == "deposit_howto")

    plan = build_faq_outbound_plan(answer_blocks=deposit["answer_blocks"], **BASE_KWARGS)

    assert plan["message_count"] == 3
    assert [message["message_kind"] for message in plan["messages"]] == ["image", "text", "buttons"]
    assert plan["messages"][0]["payload"]["asset_key"] == "deposit_howto"
    assert plan["messages"][2]["payload"] == {"menu_key": "deposit_recovery"}


def test_build_faq_outbound_plan_backend_fact_fallback_stays_text_only():
    plan = build_faq_outbound_plan(
        answer_blocks=[{"type": "text", "text": BACKEND_FACT_FALLBACK_ANSWER}],
        **BASE_KWARGS,
    )

    assert plan["message_count"] == 1
    assert plan["messages"][0]["message_kind"] == "text"
    assert plan["messages"][0]["command_type"] == "livechat.send_text"


def test_faq_outbound_plan_module_stays_side_effect_free():
    import app.services.faq_outbound_plan as faq_outbound_plan

    module_text = Path(faq_outbound_plan.__file__).read_text(encoding="utf-8")

    assert "sender_worker" not in module_text
    assert "OutboundMessageRepository" not in module_text
    assert "outbound_messages" not in module_text
    assert "LiveChat" not in module_text


def test_faq_plan_to_outbound_rows_maps_plan_messages_without_writing():
    plan = build_faq_outbound_plan(
        answer_blocks=[
            {"type": "image", "asset_key": "deposit_howto", "position": "before"},
            {"type": "text", "text": "充值说明"},
        ],
        **BASE_KWARGS,
    )

    rows = faq_plan_to_outbound_rows(
        plan,
        chat_id="chat-1",
        thread_id="thread-1",
        conversation_id="livechat:chat-1",
        inbound_event_id=11,
        tenant_id="default",
        channel_type="livechat",
    )

    assert rows == [
        {
            "tenant_id": "default",
            "channel_type": "livechat",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "conversation_id": "livechat:chat-1",
            "inbound_event_id": 11,
            "action_type": "livechat.send_image",
            "command_type": "livechat.send_image",
            "message_type": "image",
            "message_kind": "image",
            "block_index": 0,
            "dedup_key": plan["messages"][0]["dedup_key"],
            "payload_json": plan["messages"][0]["payload"],
            "status": "PENDING",
        },
        {
            "tenant_id": "default",
            "channel_type": "livechat",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "conversation_id": "livechat:chat-1",
            "inbound_event_id": 11,
            "action_type": "livechat.send_text",
            "command_type": "livechat.send_text",
            "message_type": "text",
            "message_kind": "text",
            "block_index": 1,
            "dedup_key": plan["messages"][1]["dedup_key"],
            "payload_json": plan["messages"][1]["payload"],
            "status": "PENDING",
        },
    ]
