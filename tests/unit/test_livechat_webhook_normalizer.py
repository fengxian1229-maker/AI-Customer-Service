import asyncio

import pytest

from app.channels.livechat.webhook_normalizer import (
    WebhookAuthError,
    normalize_webhook_payload,
    normalize_webhook_payload_async,
)
from app.core.settings import Settings


def make_settings(**overrides):
    values = {
        "livechat_agent_access_token": "token",
        "livechat_account_id": "account",
        "livechat_webhook_secret": "secret",
        "livechat_allowed_group_ids": "",
    }
    values.update(overrides)
    return Settings(**values)


def incoming_event_body(**overrides):
    body = {
        "webhook_id": "webhook-1",
        "secret_key": "secret",
        "action": "incoming_event",
        "organization_id": "org-1",
        "payload": {
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "access": {"group_ids": [23]},
            "users": [{"id": "customer-1", "type": "customer"}],
            "event": {
                "id": "event-1",
                "type": "message",
                "author_id": "customer-1",
                "created_at": "2026-07-06T00:00:00Z",
                "text": "hello",
            },
        },
    }
    body.update(overrides)
    return body


def test_secret_correct_normalizes_message():
    events = normalize_webhook_payload(incoming_event_body(), make_settings())

    assert len(events) == 1
    assert events[0].source == "livechat_webhook"
    assert events[0].raw_action == "incoming_event"
    assert events[0].standard_event_type == "MESSAGE_CREATED"
    assert events[0].dedup_key == "livechat_webhook:incoming_event:chat-1:thread-1:event-1"
    assert events[0].ignored is False
    assert events[0].payload_json["platform"] == "TEST"
    assert events[0].payload_json["livechat_group_id"] == 23


@pytest.mark.parametrize("secret", [None, "wrong"])
def test_secret_missing_or_wrong_raises(secret):
    body = incoming_event_body(secret_key=secret)

    with pytest.raises(WebhookAuthError):
        normalize_webhook_payload(body, make_settings())


def test_incoming_chat_with_initial_message_emits_only_message():
    body = {
        "webhook_id": "webhook-chat",
        "secret_key": "secret",
        "action": "incoming_chat",
        "organization_id": "org-1",
        "payload": {
            "chat": {
                "id": "chat-1",
                "created_at": "2026-07-06T00:00:00Z",
                "access": {"group_ids": [23]},
                "users": [{"id": "customer-1", "type": "customer"}],
                "thread": {
                    "id": "thread-1",
                    "created_at": "2026-07-06T00:00:00Z",
                    "events": [
                        {
                            "id": "event-1",
                            "type": "message",
                            "author_id": "customer-1",
                            "created_at": "2026-07-06T00:00:01Z",
                            "text": "hello",
                        }
                    ],
                },
            }
        },
    }

    events = normalize_webhook_payload(body, make_settings(livechat_allowed_group_ids="23"))

    assert [event.standard_event_type for event in events] == ["MESSAGE_CREATED"]
    assert events[0].chat_id == "chat-1"
    assert events[0].event_id == "event-1"
    assert events[0].payload_json["event"]["text"] == "hello"


def test_incoming_chat_empty_thread_emits_chat_started_intro():
    body = {
        "webhook_id": "webhook-chat",
        "secret_key": "secret",
        "action": "incoming_chat",
        "organization_id": "org-1",
        "payload": {
            "chat": {
                "id": "chat-1",
                "created_at": "2026-07-06T00:00:00Z",
                "access": {"group_ids": [23]},
                "users": [{"id": "customer-1", "type": "customer"}],
                "thread": {
                    "id": "thread-1",
                    "created_at": "2026-07-06T00:00:00Z",
                    "events": [],
                },
            }
        },
    }

    events = normalize_webhook_payload(body, make_settings(livechat_allowed_group_ids="23"))

    assert [event.standard_event_type for event in events] == ["CHAT_STARTED"]
    assert events[0].event_id == "chat_started:chat-1:thread-1"


def test_incoming_chat_self_agent_greeting_does_not_emit_chat_started_intro():
    body = {
        "webhook_id": "webhook-chat",
        "secret_key": "secret",
        "action": "incoming_chat",
        "organization_id": "org-1",
        "payload": {
            "chat": {
                "id": "chat-1",
                "created_at": "2026-07-06T00:00:00Z",
                "access": {"group_ids": [23]},
                "users": [{"id": "lingxi@goetm.com", "type": "agent"}],
                "thread": {
                    "id": "thread-1",
                    "created_at": "2026-07-06T00:00:00Z",
                    "events": [
                        {
                            "id": "event-1",
                            "type": "message",
                            "author_id": "lingxi@goetm.com",
                            "created_at": "2026-07-06T00:00:01Z",
                            "text": "hello",
                        }
                    ],
                },
            }
        },
    }

    events = normalize_webhook_payload(
        body,
        make_settings(livechat_allowed_group_ids="23", livechat_self_author_ids="lingxi@goetm.com"),
    )

    assert [event.standard_event_type for event in events] == ["MESSAGE_CREATED"]
    assert events[0].ignored is True
    assert events[0].ignore_reason == "self_message"


def test_incoming_event_file_maps_to_file_received():
    body = incoming_event_body()
    body["payload"]["event"] = {
        "id": "file-1",
        "type": "file",
        "author_id": "customer-1",
        "created_at": "2026-07-06T00:00:00Z",
        "url": "https://example.test/file.png",
    }

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.standard_event_type == "FILE_RECEIVED"
    assert event.event_type == "file"


def test_unsupported_event_type_is_ignored():
    body = incoming_event_body()
    body["payload"]["event"]["type"] = "system_message"

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.standard_event_type == "UNSUPPORTED"
    assert event.ignored is True
    assert event.ignore_reason == "unsupported_event_type"


def test_rich_message_postback_preserves_button_fields():
    body = {
        "webhook_id": "webhook-postback",
        "secret_key": "secret",
        "action": "incoming_rich_message_postback",
        "organization_id": "org-1",
        "payload": {
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "access": {"group_ids": [23]},
            "event": {
                "id": "postback-event-1",
                "author_id": "customer-1",
                "created_at": "2026-07-06T00:00:00Z",
                "postback": {"id": "withdrawal_menu", "value": "提款问题"},
            },
        },
    }

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.standard_event_type == "MESSAGE_CREATED"
    assert event.event_type == "rich_message_postback"
    assert event.payload_json["button_id"] == "withdrawal_menu"
    assert event.payload_json["postback_id"] == "withdrawal_menu"
    assert event.payload_json["event"]["postback"]["id"] == "withdrawal_menu"
    assert event.payload_json["webhook_body"]["action"] == "incoming_rich_message_postback"


def test_self_author_is_ignored():
    body = incoming_event_body()
    body["payload"]["event"]["author_id"] = "self-agent"

    event = normalize_webhook_payload(body, make_settings(livechat_self_author_ids="self-agent"))[0]

    assert event.ignored is True
    assert event.sender_role == "self_agent"
    assert event.ignore_reason == "self_message"


def test_agent_author_is_ignored():
    body = incoming_event_body()
    body["payload"]["users"] = [{"id": "agent-1", "type": "agent"}]
    body["payload"]["event"]["author_id"] = "agent-1"

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.ignored is True
    assert event.ignore_reason == "agent_message"
    assert event.payload_json["human_agent_public_reply"] is True
    assert event.payload_json["human_agent_id"] == "agent-1"


def test_internal_agent_note_does_not_mark_human_public_reply():
    body = incoming_event_body()
    body["payload"]["users"] = [{"id": "agent-1", "type": "agent"}]
    body["payload"]["event"]["author_id"] = "agent-1"
    body["payload"]["event"]["visibility"] = "internal"

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.ignored is True
    assert event.ignore_reason == "agent_message"
    assert event.payload_json.get("human_agent_public_reply") is not True


def test_incoming_event_agent_author_type_is_ignored_without_users_lookup():
    body = incoming_event_body()
    body["payload"].pop("users")
    body["payload"]["event"]["author_id"] = "agent-1"
    body["payload"]["event"]["author_type"] = "agent"

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.ignored is True
    assert event.ignore_reason == "agent_message"
    assert event.payload_json["human_agent_public_reply"] is True


def test_group_not_allowed_is_insertable_ignored_event():
    event = normalize_webhook_payload(incoming_event_body(), make_settings(livechat_allowed_group_ids="99"))[0]

    assert event.ignored is True
    assert event.ignore_reason == "group_not_allowed"


def test_unknown_group_is_ignored_by_default_platform_filter():
    body = incoming_event_body()
    body["payload"]["access"]["group_ids"] = [99]

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.ignored is True
    assert event.ignore_reason == "group_not_allowed"
    assert event.payload_json["platform"] is None


def test_official_group_maps_to_platform_by_default():
    body = incoming_event_body()
    body["payload"]["access"]["group_ids"] = [28]

    event = normalize_webhook_payload(body, make_settings())[0]

    assert event.ignored is False
    assert event.payload_json["platform"] == "ZAP69"
    assert event.payload_json["livechat_group_id"] == 28


def test_dedup_key_falls_back_to_stable_hash_when_missing_ids():
    body = incoming_event_body()
    body["payload"].pop("thread_id")
    body["payload"]["event"].pop("id")

    first = normalize_webhook_payload(body, make_settings())[0]
    second = normalize_webhook_payload(body, make_settings())[0]

    assert first.dedup_key == second.dedup_key
    assert first.dedup_key.startswith("livechat_webhook:incoming_event:chat-1:-:")


def test_async_normalizer_uses_get_chat_for_missing_group_data():
    body = incoming_event_body()
    body["payload"].pop("access")

    class FakeClient:
        async def get_chat(self, chat_id):
            assert chat_id == "chat-1"
            return {"id": "chat-1", "access": {"group_ids": [23]}, "users": [{"id": "customer-1", "type": "customer"}]}

    events = asyncio.run(normalize_webhook_payload_async(body, make_settings(livechat_allowed_group_ids="23"), FakeClient()))

    assert events[0].ignored is False
    assert events[0].payload_json["chat_lookup"]["access"]["group_ids"] == [23]


def test_async_normalizer_uses_resolved_chat_lookup_before_get_chat():
    body = incoming_event_body()
    body["payload"].pop("access")

    class FakeClient:
        async def get_chat(self, chat_id):
            raise AssertionError("get_chat should not be called when resolver returns group data")

    async def resolve(chat_id):
        assert chat_id == "chat-1"
        return {"id": "chat-1", "access": {"group_ids": [23]}}

    events = asyncio.run(
        normalize_webhook_payload_async(
            body,
            make_settings(livechat_allowed_group_ids="23"),
            client=FakeClient(),
            chat_lookup_resolver=resolve,
        )
    )

    assert events[0].ignored is False
    assert events[0].payload_json["chat_lookup"]["access"]["group_ids"] == [23]


def test_async_normalizer_uses_get_chat_for_missing_author_type_even_with_group_data():
    body = incoming_event_body()
    body["payload"].pop("users")
    body["payload"]["event"]["author_id"] = "agent-1"

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        async def get_chat(self, chat_id):
            self.calls.append(chat_id)
            return {
                "id": "chat-1",
                "access": {"group_ids": [23]},
                "users": [{"id": "agent-1", "type": "agent"}],
            }

    client = FakeClient()
    events = asyncio.run(
        normalize_webhook_payload_async(
            body,
            make_settings(livechat_allowed_group_ids="23"),
            client=client,
        )
    )

    assert client.calls == ["chat-1"]
    assert events[0].ignored is True
    assert events[0].ignore_reason == "agent_message"
    assert events[0].payload_json["human_agent_public_reply"] is True


def test_async_normalizer_marks_missing_group_when_get_chat_fails():
    body = incoming_event_body()
    body["payload"].pop("access")

    class FakeClient:
        async def get_chat(self, chat_id):
            assert chat_id == "chat-1"
            raise RuntimeError("livechat get_chat failed")

    events = asyncio.run(normalize_webhook_payload_async(body, make_settings(livechat_allowed_group_ids="23"), FakeClient()))

    assert events[0].ignored is True
    assert events[0].ignore_reason == "group_lookup_failed"
    assert events[0].payload_json["chat_lookup_error"]["type"] == "RuntimeError"
