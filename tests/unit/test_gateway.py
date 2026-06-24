import asyncio

from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService, build_fixed_reply, should_enqueue_reply


def make_inbound_event() -> InboundEvent:
    return InboundEvent(
        source="polling_fallback",
        raw_action="polling.event",
        chat_id="chat-1",
        thread_id="thread-1",
        event_id="event-1",
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="user-1",
        sender_role="external",
        occurred_at="2026-06-24 00:00:00.000000",
        dedup_key="key",
        payload_json={},
        ignored=False,
    )


def test_should_enqueue_reply_for_message_created():
    assert should_enqueue_reply(make_inbound_event()) is True


def test_build_fixed_reply_message():
    outbox = build_fixed_reply(make_inbound_event())

    assert outbox["action_type"] == "send_event"
    assert outbox["payload_json"]["text"] == "Hello, I received your message. How can I help you today?"


class FakeConversationRepository:
    def __init__(self) -> None:
        self.calls = []

    async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
        self.calls.append((chat_id, thread_id))
        return {"conversation_id": f"livechat:{chat_id}"}


class FakeOutboundRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert(self, message: dict) -> int:
        self.inserted.append(message)
        return 1


class FakeInboundRepository:
    def __init__(self) -> None:
        self.processed = []

    async def mark_processed(self, inbound_event_id: int) -> None:
        self.processed.append(inbound_event_id)


def test_gateway_service_processes_message_created():
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
    )

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    assert result["should_reply"] is True
    assert result["outbound_message"]["action_type"] == "send_event"
