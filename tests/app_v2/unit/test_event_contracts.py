from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app_v2.domain.directives import ControlDirective
from app_v2.domain.events import ContentPart, UserMessageEvent, validate_event_envelope


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def user_message_data() -> dict:
    return {
        "event_id": "evt-user-1",
        "event_type": "UserMessage",
        "tenant_id": "tenant-1",
        "agent_id": "agent-1",
        "conversation_id": "conversation-1",
        "conversation_sequence": 1,
        "occurred_at": NOW,
        "payload": {
            "message": {
                "message_id": "message-1",
                "sender_role": "customer",
                "content_part": {"content_type": "text", "content": "hello"},
                "sent_at": NOW,
            },
            "history_snapshot": [],
        },
    }


def test_validate_user_message_returns_typed_event_and_content():
    event = validate_event_envelope(user_message_data())

    assert isinstance(event, UserMessageEvent)
    assert isinstance(event.payload.message.content_part, ContentPart)
    assert event.payload.message.content_part.content_type == "text"
    assert event.payload.message.content_part.content == "hello"


def test_validate_user_message_preserves_backend_agent_id():
    data = user_message_data()

    event = validate_event_envelope(data)

    assert event.agent_id == "agent-1"


def test_validate_user_message_requires_agent_id():
    data = user_message_data()
    del data["agent_id"]

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


def test_validate_user_message_rejects_backend_runtime_version():
    data = {**user_message_data(), "runtime_version": "runtime-v1"}

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


def test_validate_user_message_rejects_removed_protocol_version():
    data = {**user_message_data(), "protocol_version": "v2.1"}

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


def test_user_message_rejects_legacy_plural_content_parts():
    data = user_message_data()
    data["payload"]["message"]["content_parts"] = {
        "content_type": "text",
        "content": "hello",
    }

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        ("text", "hello"),
        ("image", "https://media.example/image.png"),
        ("video", "video-reference"),
        ("audio", "audio-reference"),
    ],
)
def test_user_message_accepts_four_content_types_with_string_content(content_type, content):
    data = user_message_data()
    data["payload"]["message"]["content_part"] = {
        "content_type": content_type,
        "content": content,
    }

    event = validate_event_envelope(data)

    assert event.payload.message.content_part.content_type == content_type
    assert event.payload.message.content_part.content == content


@pytest.mark.parametrize(
    "invalid_content_part",
    [
        {"content_type": "file", "content": "file-reference"},
        {"content_type": "text", "content": ""},
        {"content_type": "text", "content": b"binary-is-not-accepted"},
        {"type": "text", "content": "legacy field"},
        {
            "type": "attachment",
            "attachment": {"signed_url": "https://media.example/image.png"},
        },
    ],
)
def test_user_message_rejects_unknown_empty_binary_or_legacy_content_shape(invalid_content_part):
    data = user_message_data()
    data["payload"]["message"]["content_part"] = invalid_content_part

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "ControlDirectiveResult",
            {
                "directive_id": "directive-1",
                "outcome": "SUCCEEDED",
                "completed_stages": ["notice_sent"],
            },
        ),
        (
            "ConversationStatusUpdate",
            {
                "previous_status": "HUMAN_ACTIVE",
                "new_status": "BOT_ACTIVE",
                "reason": "agent_finished",
                "effective_at": NOW,
            },
        ),
        (
            "AssistantMessage",
            {
                "text": "hello",
                "reply_language": "en",
                "response_kind": "direct_reply",
                "in_reply_to_event_ids": ["evt-user-1"],
            },
        ),
        (
            "ControlDirective",
            {
                "directive_id": "directive-1",
                "directive_type": "handoff.requested",
                "customer_notice": "I am requesting a human agent.",
            },
        ),
        (
            "ServiceError",
            {
                "error_code": "SEQUENCE_GAP",
                "retryable": True,
                "safe_message": "Sequence gap detected.",
            },
        ),
        (
            "CapabilityResultEvent",
            {
                "query_request_id": "query-1",
                "workflow_instance_id": "workflow-1",
                "outcome": "SUCCEEDED",
                "result": {"remaining_turnover": 10},
            },
        ),
        (
            "CapabilityPendingDueEvent",
            {
                "query_request_id": "query-1",
                "workflow_instance_id": "workflow-1",
            },
        ),
        (
            "ResumeDeferredMessagesEvent",
            {
                "handoff_directive_id": "directive-1",
                "deferred_event_ids": ["evt-user-2"],
            },
        ),
    ],
)
def test_validate_event_envelope_supports_complete_v1_event_catalog(event_type, payload):
    data = {
        **user_message_data(),
        "event_id": f"evt-{event_type}",
        "event_type": event_type,
        "payload": payload,
    }

    event = validate_event_envelope(data)

    assert event.event_type == event_type
    assert not isinstance(event.payload, dict)


def test_control_directive_event_reuses_the_domain_contract():
    data = {
        **user_message_data(),
        "event_id": "evt-directive-1",
        "event_type": "ControlDirective",
        "payload": {
            "directive_id": "directive-1",
            "directive_type": "handoff.requested",
            "customer_notice": "I am requesting a human agent.",
        },
    }

    event = validate_event_envelope(data)

    assert isinstance(event.payload, ControlDirective)


def test_validate_event_envelope_rejects_unknown_event_type():
    data = {**user_message_data(), "event_type": "UnknownEvent"}

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize("removed_field", ["correlation_id", "causation_id"])
def test_validate_event_envelope_rejects_removed_ambiguous_trace_fields(removed_field):
    data = {**user_message_data(), removed_field: "trace-1"}

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize(
    "removed_event_type",
    ["AssistantMessageSendResult", "IdleFollowupDueEvent", "IdleCloseDueEvent"],
)
def test_validate_event_envelope_rejects_ai_idle_and_close_events(removed_event_type):
    data = {**user_message_data(), "event_type": removed_event_type, "payload": {}}

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


def test_assistant_message_rejects_removed_idle_tracking_flag():
    data = {
        **user_message_data(),
        "event_type": "AssistantMessage",
        "payload": {
            "text": "hello",
            "reply_language": "en",
            "response_kind": "direct_reply",
            "in_reply_to_event_ids": ["evt-user-1"],
            "idle_tracking_required": True,
        },
    }

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


def test_control_directive_event_rejects_conversation_close_request():
    data = {
        **user_message_data(),
        "event_type": "ControlDirective",
        "payload": {
            "directive_id": "directive-close",
            "directive_type": "conversation.close_requested",
            "customer_notice": "Closing this conversation.",
        },
    }

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize(
    ("event_type", "payload", "removed_field", "removed_value"),
    [
        (
            "ControlDirectiveResult",
            {"directive_id": "directive-1", "outcome": "SUCCEEDED", "completed_stages": []},
            "occurred_at",
            NOW,
        ),
        (
            "ConversationStatusUpdate",
            {
                "previous_status": "HUMAN_ACTIVE",
                "new_status": "BOT_ACTIVE",
                "reason": "agent_finished",
                "effective_at": NOW,
            },
            "status_update_id",
            "status-1",
        ),
        (
            "ControlDirective",
            {
                "directive_id": "directive-1",
                "directive_type": "handoff.requested",
                "customer_notice": "I am requesting a human agent.",
            },
            "idempotency_key",
            "handoff:directive-1",
        ),
    ],
)
def test_event_payloads_reject_removed_duplicate_fields(event_type, payload, removed_field, removed_value):
    data = {
        **user_message_data(),
        "event_id": f"evt-{event_type}",
        "event_type": event_type,
        "payload": {**payload, removed_field: removed_value},
    }

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize(
    ("event_type", "payload", "removed_field", "removed_value"),
    [
        (
            "ControlDirective",
            {
                "directive_id": "directive-1",
                "directive_type": "handoff.requested",
                "customer_notice": "I am requesting a human agent.",
            },
            "expected_session_version",
            1,
        ),
        (
            "ControlDirective",
            {
                "directive_id": "directive-1",
                "directive_type": "handoff.requested",
                "customer_notice": "I am requesting a human agent.",
            },
            "preconditions",
            {"service_status": "BOT_ACTIVE"},
        ),
        (
            "CapabilityPendingDueEvent",
            {"query_request_id": "query-1", "workflow_instance_id": "workflow-1"},
            "scheduled_event_id",
            "schedule-1",
        ),
    ],
)
def test_event_payloads_reject_removed_internal_state_fields(event_type, payload, removed_field, removed_value):
    data = {
        **user_message_data(),
        "event_id": f"evt-{event_type}",
        "event_type": event_type,
        "payload": {**payload, removed_field: removed_value},
    }

    with pytest.raises(ValidationError):
        validate_event_envelope(data)


@pytest.mark.parametrize("history_problem", ["contains_current", "duplicate_id", "out_of_order"])
def test_user_message_rejects_invalid_history_snapshot(history_problem):
    data = user_message_data()
    earlier = {
        "message_id": "history-1",
        "sender_role": "ai_assistant",
        "content_part": {"content_type": "text", "content": "first"},
        "sent_at": datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
    }
    later = {
        **earlier,
        "message_id": "history-2",
        "content_part": {"content_type": "text", "content": "second"},
        "sent_at": datetime(2026, 7, 17, 11, 30, tzinfo=UTC),
    }
    if history_problem == "contains_current":
        history = [{**earlier, "message_id": "message-1"}]
    elif history_problem == "duplicate_id":
        history = [earlier, {**later, "message_id": "history-1"}]
    else:
        history = [later, earlier]
    data["payload"]["history_snapshot"] = history

    with pytest.raises(ValidationError):
        validate_event_envelope(data)
