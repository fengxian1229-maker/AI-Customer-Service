import asyncio

import pytest

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

    class FakeKnowledgeRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeCheckpointRunRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeRagService:
        def __init__(self, knowledge_repository=None) -> None:
            self.knowledge_repository = knowledge_repository

    class FakeService:
        def __init__(
            self,
            transactional_repository=None,
            checkpointer=None,
            rag_service=None,
            checkpoint_mode="off",
            checkpoint_run_repository=None,
        ) -> None:
            self.transactional_repository = transactional_repository
            self.checkpointer = checkpointer
            self.rag_service = rag_service
            self.checkpoint_mode = checkpoint_mode
            self.checkpoint_run_repository = checkpoint_run_repository

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
    monkeypatch.setattr(gateway_consumer, "KnowledgeDocumentRepository", FakeKnowledgeRepository)
    monkeypatch.setattr(gateway_consumer, "GraphCheckpointRunRepository", FakeCheckpointRunRepository)
    monkeypatch.setattr(gateway_consumer, "RagService", FakeRagService)
    monkeypatch.setattr(gateway_consumer, "GatewayService", FakeService)
    class FakeManagedCheckpointer:
        def __init__(self) -> None:
            self.checkpointer = None

        def close(self) -> None:
            return None

    monkeypatch.setattr(gateway_consumer, "build_checkpointer", lambda mode, settings=None: FakeManagedCheckpointer())

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


def test_process_next_batch_passes_off_checkpointer_to_gateway_service(monkeypatch):
    from app.workers import gateway_consumer

    calls = {}

    class FakeInboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def fetch_unprocessed(self, limit: int = 20) -> list[dict]:
            return []

    class FakeTransactionalRepository:
        def __init__(self, pool, inbound_repository=None) -> None:
            self.pool = pool

    class FakeKnowledgeRepository:
        def __init__(self, pool) -> None:
            calls["knowledge_pool"] = pool

    class FakeCheckpointRunRepository:
        def __init__(self, pool) -> None:
            calls["checkpoint_pool"] = pool

    class FakeRagService:
        def __init__(self, knowledge_repository=None) -> None:
            calls["knowledge_repository"] = knowledge_repository

    class FakeService:
        def __init__(
            self,
            transactional_repository=None,
            checkpointer=None,
            rag_service=None,
            checkpoint_mode="off",
            checkpoint_run_repository=None,
        ) -> None:
            calls["checkpointer"] = checkpointer
            calls["rag_service"] = rag_service
            calls["checkpoint_mode"] = checkpoint_mode
            calls["checkpoint_run_repository"] = checkpoint_run_repository

    class FakeManagedCheckpointer:
        def __init__(self) -> None:
            self.checkpointer = None

        def close(self) -> None:
            return None

    def fake_build_checkpointer(mode: str, settings=None):
        calls["mode"] = mode
        calls["settings"] = settings
        return FakeManagedCheckpointer()

    monkeypatch.setattr(gateway_consumer, "InboundEventRepository", FakeInboundRepository)
    monkeypatch.setattr(gateway_consumer, "GatewayTransactionRepository", FakeTransactionalRepository)
    monkeypatch.setattr(gateway_consumer, "KnowledgeDocumentRepository", FakeKnowledgeRepository)
    monkeypatch.setattr(gateway_consumer, "GraphCheckpointRunRepository", FakeCheckpointRunRepository)
    monkeypatch.setattr(gateway_consumer, "RagService", FakeRagService)
    monkeypatch.setattr(gateway_consumer, "GatewayService", FakeService)
    monkeypatch.setattr(gateway_consumer, "build_checkpointer", fake_build_checkpointer)

    result = asyncio.run(gateway_consumer.process_next_batch(pool=object(), limit=20, checkpoint_mode="off"))

    assert calls["mode"] == "off"
    assert calls["checkpointer"] is None
    assert calls["checkpoint_mode"] == "off"
    assert calls["knowledge_pool"] is not None
    assert calls["checkpoint_pool"] is not None
    assert isinstance(calls["rag_service"], FakeRagService)
    assert isinstance(calls["knowledge_repository"], FakeKnowledgeRepository)
    assert isinstance(calls["checkpoint_run_repository"], FakeCheckpointRunRepository)
    assert result["processed"] == 0


def test_process_next_batch_builds_memory_checkpointer(monkeypatch):
    from app.workers import gateway_consumer

    calls = {}
    fake_checkpointer = object()

    class FakeInboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def fetch_unprocessed(self, limit: int = 20) -> list[dict]:
            return []

    class FakeTransactionalRepository:
        def __init__(self, pool, inbound_repository=None) -> None:
            self.pool = pool

    class FakeKnowledgeRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeCheckpointRunRepository:
        def __init__(self, pool) -> None:
            calls["checkpoint_pool"] = pool

    class FakeRagService:
        def __init__(self, knowledge_repository=None) -> None:
            self.knowledge_repository = knowledge_repository

    class FakeService:
        def __init__(
            self,
            transactional_repository=None,
            checkpointer=None,
            rag_service=None,
            checkpoint_mode="off",
            checkpoint_run_repository=None,
        ) -> None:
            calls["checkpointer"] = checkpointer
            calls["checkpoint_mode"] = checkpoint_mode
            calls["checkpoint_run_repository"] = checkpoint_run_repository

    class FakeManagedCheckpointer:
        def __init__(self, checkpointer) -> None:
            self.checkpointer = checkpointer
            self.closed = False

        def close(self) -> None:
            self.closed = True
            calls["closed"] = True

    def fake_build_checkpointer(mode: str, settings=None):
        calls["mode"] = mode
        calls["settings"] = settings
        return FakeManagedCheckpointer(fake_checkpointer)

    monkeypatch.setattr(gateway_consumer, "InboundEventRepository", FakeInboundRepository)
    monkeypatch.setattr(gateway_consumer, "GatewayTransactionRepository", FakeTransactionalRepository)
    monkeypatch.setattr(gateway_consumer, "KnowledgeDocumentRepository", FakeKnowledgeRepository)
    monkeypatch.setattr(gateway_consumer, "GraphCheckpointRunRepository", FakeCheckpointRunRepository)
    monkeypatch.setattr(gateway_consumer, "RagService", FakeRagService)
    monkeypatch.setattr(gateway_consumer, "GatewayService", FakeService)
    monkeypatch.setattr(gateway_consumer, "build_checkpointer", fake_build_checkpointer)

    pool = object()
    asyncio.run(gateway_consumer.process_next_batch(pool=pool, limit=20, checkpoint_mode="memory"))

    assert calls["mode"] == "memory"
    assert calls["checkpoint_pool"] is pool
    assert calls["checkpointer"] is fake_checkpointer
    assert calls["checkpoint_mode"] == "memory"
    assert calls["closed"] is True
    assert isinstance(calls["checkpoint_run_repository"], FakeCheckpointRunRepository)


def test_process_next_batch_mysql_provider_failure_does_not_fetch_inbound(monkeypatch):
    from app.workers import gateway_consumer

    class FakeInboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def fetch_unprocessed(self, limit: int = 20) -> list[dict]:
            raise AssertionError("fetch_unprocessed should not be called")

    class FakeTransactionalRepository:
        def __init__(self, pool, inbound_repository=None) -> None:
            self.pool = pool

    class FakeKnowledgeRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeCheckpointRunRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeRagService:
        def __init__(self, knowledge_repository=None) -> None:
            self.knowledge_repository = knowledge_repository

    monkeypatch.setattr(gateway_consumer, "InboundEventRepository", FakeInboundRepository)
    monkeypatch.setattr(gateway_consumer, "GatewayTransactionRepository", FakeTransactionalRepository)
    monkeypatch.setattr(gateway_consumer, "KnowledgeDocumentRepository", FakeKnowledgeRepository)
    monkeypatch.setattr(gateway_consumer, "GraphCheckpointRunRepository", FakeCheckpointRunRepository)
    monkeypatch.setattr(gateway_consumer, "RagService", FakeRagService)
    monkeypatch.setattr(gateway_consumer, "build_checkpointer", lambda mode, settings=None: (_ for _ in ()).throw(RuntimeError("mysql init failed")))

    with pytest.raises(RuntimeError, match="mysql init failed"):
        asyncio.run(gateway_consumer.process_next_batch(pool=object(), limit=20, checkpoint_mode="mysql", settings=object()))


def test_process_next_batch_builds_gemini_provider_and_reports_llm_summary(monkeypatch):
    from app.workers import gateway_consumer

    calls = {}
    fake_provider = object()

    class FakeInboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def fetch_unprocessed(self, limit: int = 20) -> list[dict]:
            return []

    class FakeTransactionalRepository:
        def __init__(self, pool, inbound_repository=None) -> None:
            self.pool = pool

    class FakeKnowledgeRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeCheckpointRunRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

    class FakeRagService:
        def __init__(self, knowledge_repository=None) -> None:
            self.knowledge_repository = knowledge_repository

    class FakeService:
        def __init__(self, **kwargs) -> None:
            calls["service_kwargs"] = kwargs

    class FakeManagedCheckpointer:
        def __init__(self) -> None:
            self.checkpointer = None

        def close(self) -> None:
            return None

    class FakeSettings:
        llm_provider = "gemini"
        gemini_model = "gemini-3.1-flash-lite"
        gemini_project = "project-gemini-0306"
        gemini_location = "global"
        gemini_vertexai = True
        llm_rewrite_shadow_enabled = True
        llm_rewrite_fallback_enabled = False
        llm_intent_shadow_enabled = True
        llm_intent_fallback_enabled = False
        llm_intent_min_confidence = 0.75

    monkeypatch.setattr(gateway_consumer, "InboundEventRepository", FakeInboundRepository)
    monkeypatch.setattr(gateway_consumer, "GatewayTransactionRepository", FakeTransactionalRepository)
    monkeypatch.setattr(gateway_consumer, "KnowledgeDocumentRepository", FakeKnowledgeRepository)
    monkeypatch.setattr(gateway_consumer, "GraphCheckpointRunRepository", FakeCheckpointRunRepository)
    monkeypatch.setattr(gateway_consumer, "RagService", FakeRagService)
    monkeypatch.setattr(gateway_consumer, "GatewayService", FakeService)
    monkeypatch.setattr(gateway_consumer, "build_checkpointer", lambda mode, settings=None: FakeManagedCheckpointer())

    def fake_build_llm_provider(mode: str, settings=None):
        calls["provider_mode"] = mode
        calls["provider_settings"] = settings
        return fake_provider

    monkeypatch.setattr(gateway_consumer, "build_llm_provider", fake_build_llm_provider)

    result = asyncio.run(
        gateway_consumer.process_next_batch(
            pool=object(),
            limit=20,
            checkpoint_mode="off",
            settings=FakeSettings(),
        )
    )

    assert calls["provider_mode"] == "gemini"
    assert calls["provider_settings"].gemini_project == "project-gemini-0306"
    assert calls["service_kwargs"]["llm_rewrite_service"] is fake_provider
    assert calls["service_kwargs"]["llm_intent_service"] is fake_provider
    assert result["llm"] == {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "vertexai": True,
        "project": "project-gemini-0306",
        "location": "global",
        "rewrite_shadow_enabled": True,
        "intent_shadow_enabled": True,
        "rewrite_fallback_enabled": False,
        "intent_fallback_enabled": False,
        "fallback_enabled": False,
        "shadow_active": True,
    }
