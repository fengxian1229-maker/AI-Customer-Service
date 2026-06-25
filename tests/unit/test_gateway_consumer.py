import asyncio

from app.schemas.events import InboundEvent


def make_row(event_id: str, text: str) -> dict:
    return {
        "id": int(event_id.split("-")[-1]),
        "source": "polling_fallback",
        "raw_action": "polling.event",
        "chat_id": f"chat-{event_id}",
        "thread_id": f"thread-{event_id}",
        "event_id": event_id,
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "author_id": "user-1",
        "sender_role": "external",
        "occurred_at": "2026-06-24 00:00:00.000000",
        "dedup_key": f"dedup:{event_id}",
        "payload_json": {"event": {"type": "message", "text": text}},
        "organization_id": None,
        "ignored": False,
        "ignore_reason": None,
    }


def test_process_next_batch_continues_after_single_event_failure(monkeypatch):
    from app.workers import gateway_consumer

    calls = []

    class FakeInboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def fetch_unprocessed(self, limit: int = 20) -> list[dict]:
            assert limit == 20
            return [
                make_row("event-1", "first"),
                make_row("event-2", "second"),
                make_row("event-3", "third"),
            ]

    class FakeTransactionalRepository:
        def __init__(self, pool, inbound_repository=None) -> None:
            self.pool = pool
            self.inbound_repository = inbound_repository

    class FakeService:
        def __init__(self, transactional_repository=None) -> None:
            self.transactional_repository = transactional_repository

        async def process_event(self, inbound_event_id: int, event: InboundEvent) -> dict:
            calls.append((inbound_event_id, event.event_id))
            if inbound_event_id == 2:
                raise RuntimeError("graph exploded")
            return {
                "outbound_message": {"id": inbound_event_id} if inbound_event_id == 3 else None,
                "event_id": event.event_id,
            }

    monkeypatch.setattr(gateway_consumer, "InboundEventRepository", FakeInboundRepository)
    monkeypatch.setattr(gateway_consumer, "GatewayTransactionRepository", FakeTransactionalRepository)
    monkeypatch.setattr(gateway_consumer, "GatewayService", FakeService)

    results = asyncio.run(gateway_consumer.process_next_batch(pool=object(), limit=20))

    assert calls == [(1, "event-1"), (2, "event-2"), (3, "event-3")]
    assert results["processed"] == 2
    assert results["failed"] == 1
    assert results["enqueued"] == 1
    assert [item["event_id"] for item in results["results"]] == ["event-1", "event-3"]
    assert results["failures"] == [
        {
            "inbound_event_id": 2,
            "event_id": "event-2",
            "error_type": "RuntimeError",
            "error_message": "graph exploded",
        }
    ]
