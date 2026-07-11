from app.channels.text_com.adapter import TextComChannelAdapter
from app.core.config import Settings
from app.domain.messages import InboundEventType


def make_adapter() -> TextComChannelAdapter:
    return TextComChannelAdapter(
        Settings(
            text_com_webhook_secret="secret",
            default_tenant_id="tenant_x",
            text_com_ignored_author_ids="bot@example.com",
        )
    )


def test_incoming_event_message_normalized():
    body = {
        "webhook_id": "wh_1",
        "secret_key": "secret",
        "action": "incoming_event",
        "organization_id": "org_1",
        "payload": {
            "chat_id": "chat_1",
            "thread_id": "thread_1",
            "event": {
                "id": "event_1",
                "type": "message",
                "text": "hello",
                "author_id": "customer_1",
                "created_at": "2026-06-22T12:00:00Z",
            },
        },
    }

    events = make_adapter().parse_webhook(body)

    assert len(events) == 1
    event = events[0]
    assert event.event_id == "event_1"
    assert event.event_type == InboundEventType.MESSAGE_CREATED
    assert event.text == "hello"
    assert event.chat_id == "chat_1"
    assert event.thread_id == "thread_1"
    assert event.channel.tenant_id == "tenant_x"


def test_incoming_chat_initial_events_normalized():
    body = {
        "webhook_id": "wh_2",
        "secret_key": "secret",
        "action": "incoming_chat",
        "organization_id": "org_1",
        "payload": {
            "chat": {
                "id": "chat_2",
                "users": [{"id": "customer_2", "type": "customer"}],
                "thread": {
                    "id": "thread_2",
                    "events": [
                        {
                            "id": "event_2",
                            "type": "message",
                            "text": "first message",
                            "author_id": "customer_2",
                        }
                    ],
                },
            }
        },
    }

    events = make_adapter().parse_webhook(body)

    assert len(events) == 2
    assert events[0].event_type == InboundEventType.CHAT_STARTED
    assert events[1].event_type == InboundEventType.MESSAGE_CREATED
    assert events[1].customer_id == "customer_2"


def test_ignored_author_skipped():
    body = {
        "webhook_id": "wh_3",
        "secret_key": "secret",
        "action": "incoming_event",
        "organization_id": "org_1",
        "payload": {
            "chat_id": "chat_1",
            "thread_id": "thread_1",
            "event": {
                "id": "event_3",
                "type": "message",
                "text": "bot echo",
                "author_id": "bot@example.com",
            },
        },
    }

    assert make_adapter().parse_webhook(body) == []
