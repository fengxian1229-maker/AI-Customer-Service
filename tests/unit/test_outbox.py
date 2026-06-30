from app.services.outbox import build_command_outbox
from app.workflows.command_contracts import CommandType


def test_build_command_outbox_preserves_livechat_send_text_payload():
    row = build_command_outbox(
        chat_id="chat-1",
        thread_id="thread-1",
        conversation_id="livechat:chat-1",
        inbound_event_id=77,
        command={
            "type": CommandType.LIVECHAT_SEND_TEXT,
            "payload": {"text": "我会为你转接真人客服继续协助。", "handoff_ack": True},
        },
    )

    assert row["payload_json"] == {
        "type": "message",
        "text": "我会为你转接真人客服继续协助。",
        "handoff_ack": True,
    }
