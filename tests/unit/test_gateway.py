import asyncio

import pytest

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
        payload_json={"event": {"type": "message", "text": "mi deposito no llegó"}},
        ignored=False,
    )


def make_event_with_text(text: str) -> InboundEvent:
    event = make_inbound_event()
    event.payload_json = {"event": {"type": "message", "text": text}}
    return event


def test_should_enqueue_reply_for_message_created():
    assert should_enqueue_reply(make_inbound_event()) is True


def test_build_fixed_reply_message():
    outbox = build_fixed_reply(make_inbound_event())

    assert outbox["action_type"] == "send_event"
    assert outbox["payload_json"]["text"] == "Hello, I received your message. How can I help you today?"


class FakeConversationRepository:
    def __init__(self) -> None:
        self.calls = []
        self.updated = []

    async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
        self.calls.append((chat_id, thread_id))
        return {
            "conversation_id": f"livechat:{chat_id}",
            "tenant_id": "default",
            "channel_type": "livechat",
            "chat_id": chat_id,
            "current_thread_id": thread_id,
            "status": "AI_ACTIVE",
            "active_workflow": None,
            "workflow_stage": None,
            "slot_memory": {},
        }

    async def update_workflow_state(self, conversation_id: str, graph_state: dict) -> None:
        self.updated.append((conversation_id, graph_state))


class FakeOutboundRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert(self, message: dict) -> int:
        self.inserted.append(message)
        return 1

    async def insert_idempotent(self, message: dict) -> dict:
        self.inserted.append(message)
        return {"inserted": True, "duplicate": False, "id": 1}


class FakeInboundRepository:
    def __init__(self) -> None:
        self.processed = []

    async def mark_processed(self, inbound_event_id: int) -> None:
        self.processed.append(inbound_event_id)


class FakeExternalCommandRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert_idempotent(self, command: dict) -> dict:
        self.inserted.append(command)
        return {"inserted": True, "duplicate": False, "id": len(self.inserted)}


class FakeGraphRunErrorRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert(self, error_record: dict) -> int:
        self.inserted.append(error_record)
        return len(self.inserted)


class FakeConversationMessageRepository:
    def __init__(self, recent_messages=None) -> None:
        self.recent_messages = recent_messages or []
        self.inserted = []
        self.fetch_calls = []

    async def fetch_recent(self, conversation_id: str, limit: int = 10) -> list[dict]:
        self.fetch_calls.append((conversation_id, limit))
        return list(self.recent_messages)

    async def insert_idempotent(self, message: dict) -> dict:
        self.inserted.append(message)
        return {"inserted": True, "duplicate": False, "id": len(self.inserted)}


class FakeCheckpointRunRepository:
    def __init__(
        self,
        fail_on_insert: bool = False,
        fail_on_mark: bool = False,
        fail_on_mark_failed: bool = False,
    ) -> None:
        self.fail_on_insert = fail_on_insert
        self.fail_on_mark = fail_on_mark
        self.fail_on_mark_failed = fail_on_mark_failed
        self.inserted = []
        self.succeeded = []
        self.failed = []

    async def insert_run(self, record: dict) -> int:
        if self.fail_on_insert:
            raise RuntimeError("checkpoint metadata insert failed")
        self.inserted.append(record)
        return 91

    async def mark_succeeded(self, run_id: int, latest_checkpoint_id: str | None = None, metadata_json: dict | None = None) -> None:
        if self.fail_on_mark:
            raise RuntimeError("checkpoint metadata success failed")
        self.succeeded.append((run_id, latest_checkpoint_id, metadata_json))

    async def mark_failed(self, run_id: int, error) -> None:
        if self.fail_on_mark_failed:
            raise RuntimeError("checkpoint metadata failed-mark failed")
        self.failed.append((run_id, type(error).__name__, str(error)))


class FakeRagService:
    def __init__(self, context=None, error: Exception | None = None) -> None:
        self.context = context or {
            "documents": [
                {
                    "id": 1,
                    "title": "Bonus rules",
                    "content": "奖金规则以活动页面说明为准。",
                    "score": 5,
                }
            ],
            "source": "knowledge_documents",
            "fallback_reason": None,
        }
        self.error = error
        self.calls = []

    async def retrieve(self, state: dict) -> dict:
        self.calls.append(state)
        if self.error:
            raise self.error
        return self.context


class FakeLLMRewriteService:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "rewritten_question": "shadow rewrite",
            "normalized_query": "shadow rewrite",
            "language": "es",
            "preserved_entities": ["andy123"],
            "missing_or_ambiguous": [],
            "risk_flags": [],
            "confidence": 0.9,
            "reason": "shadow-only",
            "provider": "mock",
            "mode": "shadow",
        }
        self.calls = []

    async def rewrite(self, payload: dict) -> dict:
        self.calls.append(payload)
        return self.result


class FakeLLMIntentService:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "intent": "deposit_howto",
            "route": "faq",
            "confidence": 0.88,
            "reason": "shadow-only",
            "sop_name": None,
            "faq_query": "how to deposit",
            "risk_level": None,
            "provider": "mock",
            "mode": "shadow",
        }
        self.calls = []

    async def classify_intent(self, payload: dict) -> dict:
        self.calls.append(payload)
        return self.result


class FakeLLMRouterService:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {
            "rewritten_question": "how to deposit",
            "normalized_query": "how to deposit",
            "language": "en",
            "intent": "deposit_howto",
            "route": "faq",
            "confidence": 0.95,
            "sop_name": None,
            "faq_query": "how to deposit",
            "risk_level": None,
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "authoritative route",
            "provider": "mock",
            "mode": "guarded_authoritative",
        }
        self.error = error
        self.calls = []

    async def route(self, payload: dict) -> dict:
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.result


class ExplodingLLMService:
    async def rewrite(self, payload: dict) -> dict:
        raise RuntimeError("shadow failed with api_key=hidden")

    async def classify_intent(self, payload: dict) -> dict:
        raise RuntimeError("shadow failed with password=hidden")

    async def route(self, payload: dict) -> dict:
        raise RuntimeError("router failed with api_key=hidden")


def test_gateway_service_processes_message_created():
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository(
        recent_messages=[{"sender_role": "assistant", "message_type": "text", "text_content": "history"}]
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        message_repository=message_repository,
    )

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    assert result["should_reply"] is True
    assert result["outbound_message"]["action_type"] == "send_event"
    assert result["outbound_message"]["payload_json"]["text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_missing"
    assert result["graph_state"]["recent_messages"][0]["text_content"] == "history"
    assert conversation_repository.updated[0][1]["active_workflow"] == "deposit_missing"
    assert message_repository.fetch_calls == [("livechat:chat-1", 10)]
    assert message_repository.inserted[0]["sender_role"] == "customer"
    assert message_repository.inserted[0]["message_type"] == "text"
    assert message_repository.inserted[0]["inbound_event_id"] == 11
    assert result["graph_state"]["llm_rewrite_result"] is None
    assert result["graph_state"]["llm_intent_result"] is None
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["route_source"] == "deterministic"


def test_gateway_splits_livechat_outbox_and_external_commands():
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
    )
    event = make_inbound_event()
    event.payload_json = {
        "event": {"type": "message", "text": "mi usuario es andy123, deposito no llegó"},
        "attachments": [{"url": "https://cdn.example/deposit.png"}],
    }

    result = asyncio.run(service.process_event(11, event))

    assert [message["action_type"] for message in outbound_repository.inserted] == ["send_event"]
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "已收到你的存款案件资料，我们会继续确认，有更新会在这里通知你。"
    assert [command["command_type"] for command in external_repository.inserted] == ["telegram.send_case_card"]
    assert result["external_commands"][0]["command_type"] == "telegram.send_case_card"


def test_gateway_service_rag_faq_writes_outbound_without_external_command():
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
        message_repository=FakeConversationMessageRepository(),
        rag_service=FakeRagService(),
    )
    event = make_inbound_event()
    event.payload_json = {"event": {"type": "message", "text": "bonus rules"}}

    result = asyncio.run(service.process_event(13, event))

    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["rag_result"]["matched"] is True
    assert result["graph_state"]["rag_context"]["source"] == "knowledge_documents"
    assert result["outbound_messages"][0]["payload_json"]["text"] == result["graph_state"]["response_text"]
    assert result["external_commands"] == []
    assert external_repository.inserted == []


def test_gateway_service_human_handoff_keeps_existing_external_command_semantics_without_rag_prefetch():
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    rag_service = FakeRagService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
        message_repository=FakeConversationMessageRepository(),
        rag_service=rag_service,
    )

    result = asyncio.run(service.process_event(13, make_event_with_text("I want human agent")))

    assert rag_service.calls == []
    assert result["graph_state"]["route"] == "human_handoff"
    assert [command["command_type"] for command in result["external_commands"]] == ["human_handoff.requested"]


def test_gateway_service_prefetches_rag_context_only_for_faq_route():
    graph = RecordingGraph()
    rag_service = FakeRagService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=graph,
        rag_service=rag_service,
    )

    asyncio.run(service.process_event(14, make_event_with_text("how to deposit")))

    state, _config = graph.calls[0]
    assert rag_service.calls[0]["conversation_id"] == "livechat:chat-1"
    assert state["rag_context"]["documents"][0]["title"] == "Bonus rules"


def test_gateway_service_does_not_prefetch_rag_context_for_sop_route():
    graph = RecordingGraph()
    rag_service = FakeRagService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=graph,
        rag_service=rag_service,
    )

    asyncio.run(service.process_event(14, make_event_with_text("mi deposito no llegó")))

    state, _config = graph.calls[0]
    assert rag_service.calls == []
    assert state.get("rag_context") is None


def test_gateway_service_does_not_prefetch_rag_context_for_human_handoff_route():
    graph = RecordingGraph()
    rag_service = FakeRagService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=graph,
        rag_service=rag_service,
    )

    asyncio.run(service.process_event(14, make_event_with_text("I want human agent")))

    state, _config = graph.calls[0]
    assert rag_service.calls == []
    assert state.get("rag_context") is None


def test_gateway_service_does_not_prefetch_rag_context_for_backend_fact_guard():
    graph = RecordingGraph()
    from app.services.rag import BACKEND_FACT_FALLBACK_ANSWER, RagService

    class FakeKnowledgeRepository:
        def __init__(self) -> None:
            self.calls = []

        async def search(self, tenant_id: str, query: str, kb_scope: str = "default", limit: int = 3):
            self.calls.append((tenant_id, query, kb_scope, limit))
            return []

    repository = FakeKnowledgeRepository()
    rag_service = RagService(repository)
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=graph,
        rag_service=rag_service,
    )

    result = asyncio.run(service.process_event(14, make_event_with_text("my withdrawal status and balance")))

    state, _config = graph.calls[0]
    assert repository.calls == []
    assert state["rag_context"]["fallback_reason"] == "backend_fact"
    assert state["rag_context"]["answer"] == BACKEND_FACT_FALLBACK_ANSWER
    assert result["graph_state"]["response_text"] == "ok"


def test_gateway_service_records_error_and_skips_side_effects_when_rag_retrieve_fails():
    inbound_repository = FakeInboundRepository()
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    graph_error_repository = FakeGraphRunErrorRepository()
    message_repository = FakeConversationMessageRepository()
    graph = RecordingGraph()
    service = GatewayService(
        inbound_repository=inbound_repository,
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
        graph_run_error_repository=graph_error_repository,
        message_repository=message_repository,
        workflow_graph=graph,
        rag_service=FakeRagService(error=RuntimeError("rag retrieve failed")),
    )

    try:
        asyncio.run(service.process_event(15, make_event_with_text("how to deposit")))
    except RuntimeError as exc:
        assert str(exc) == "rag retrieve failed"
    else:
        raise AssertionError("expected rag retrieve to fail")

    assert graph.calls == []
    assert inbound_repository.processed == []
    assert conversation_repository.updated == []
    assert message_repository.inserted == []
    assert outbound_repository.inserted == []
    assert external_repository.inserted == []
    assert graph_error_repository.inserted[0]["error_type"] == "RuntimeError"
    assert graph_error_repository.inserted[0]["error_message"] == "rag retrieve failed"
    assert "rag_context" not in graph_error_repository.inserted[0]["state_snapshot"]


def test_gateway_service_without_rag_service_keeps_static_fallback_for_faq():
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
    )

    result = asyncio.run(service.process_event(16, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["rag_result"]["matched"] is True
    assert result["external_commands"] == []
    assert result["outbound_messages"][0]["payload_json"]["text"] == result["graph_state"]["response_text"]


def test_gateway_service_guarded_authoritative_blocks_llm_faq_for_deterministic_sop_without_generating_reply_or_commands():
    router_service = FakeLLMRouterService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="guarded_authoritative",
    )

    result = asyncio.run(service.process_event(26, make_event_with_text("mi deposito no llegó")))

    assert router_service.calls == []
    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["route_source"] == "deterministic"
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_missing"
    assert result["graph_state"]["llm_router_result"]["status"] == "fallback"
    assert result["graph_state"]["llm_router_result"]["fallback_reason"] == "hard_guard"
    assert result["graph_state"]["llm_router_result"]["hard_guard"] == "deterministic_sop"
    assert result["outbound_messages"][0]["message_type"] == "text"
    assert result["outbound_messages"][0]["payload_json"]["text"] == result["graph_state"]["response_text"]
    assert "response_text" not in result["graph_state"]["llm_router_result"]
    assert "commands" not in result["graph_state"]["llm_router_result"]


def test_gateway_service_faq_authoritative_calls_llm_before_keyword_router_and_uses_answer_blocks():
    router_service = FakeLLMRouterService(
        {
            "rewritten_question": "mi deposito no llegó",
            "normalized_query": "deposit not arrived",
            "language": "es",
            "intent": "faq_general",
            "route": "faq",
            "confidence": 0.95,
            "sop_name": None,
            "faq_query": "deposit not arrived FAQ",
            "risk_level": None,
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "FAQ route for smoke",
            "provider": "mock",
            "mode": "faq_authoritative",
        }
    )
    rag_service = FakeRagService(
        {
            "matched": True,
            "answer": "FAQ answer should not be rewritten.",
            "answer_blocks": [{"type": "text", "text": "FAQ answer should not be rewritten."}],
            "documents": [{"id": 1, "title": "Deposit FAQ", "score": 12}],
            "fallback_reason": None,
            "source": "knowledge_documents",
            "query": "deposit not arrived FAQ",
            "tenant_id": "default",
            "kb_scope": "default",
        }
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="faq_authoritative",
        rag_service=rag_service,
    )

    result = asyncio.run(service.process_event(34, make_event_with_text("mi deposito no llegó")))

    assert len(router_service.calls) == 1
    assert router_service.calls[0]["router_mode"] == "faq_authoritative"
    assert router_service.calls[0]["deterministic_route"] is None
    assert router_service.calls[0]["deterministic_intent_result"] is None
    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["route_source"] == "llm_faq_authoritative"
    assert result["graph_state"]["rewrite_source"] == "llm_faq_authoritative"
    assert result["graph_state"]["intent_result"]["faq_query"] == "deposit not arrived FAQ"
    assert rag_service.calls[0]["intent_result"]["faq_query"] == "deposit not arrived FAQ"
    assert rag_service.calls[0]["rag_backend_fact_guard_enabled"] is False
    assert len(result["outbound_messages"]) == 1
    assert result["outbound_messages"][0]["message_type"] == "text"
    assert result["outbound_messages"][0]["command_type"] == "livechat.send_text"
    assert result["outbound_messages"][0]["payload_json"] == {"text": "FAQ answer should not be rewritten."}
    assert result["external_commands"] == []


def test_gateway_service_faq_authoritative_rejects_sop_route_without_deterministic_fallback():
    router_service = FakeLLMRouterService(
        {
            "rewritten_question": "mi deposito no llegó",
            "normalized_query": "mi deposito no llegó",
            "language": "es",
            "intent": "deposit_howto",
            "route": "SOP",
            "confidence": 0.95,
            "sop_name": "deposit_missing",
            "faq_query": None,
            "risk_level": "elevated",
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "bad model route",
            "provider": "mock",
            "mode": "faq_authoritative",
        }
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="faq_authoritative",
        rag_service=FakeRagService(),
    )

    result = asyncio.run(service.process_event(36, make_event_with_text("mi deposito no llegó")))

    assert result["graph_state"]["route"] == "clarification"
    assert result["graph_state"]["intent_result"]["intent"] == "clarification_needed"
    assert result["graph_state"]["llm_router_result"]["fallback_reason"] == "unsupported_route"
    assert result["graph_state"]["llm_router_result"]["fallback_to_deterministic"] is False
    assert result["external_commands"] == []


def test_gateway_service_faq_authoritative_active_workflow_falls_back_to_clarification_without_sop():
    class ActiveWorkflowConversationRepository(FakeConversationRepository):
        async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
            conversation = await super().get_or_create(chat_id, thread_id)
            conversation["active_workflow"] = "deposit_missing"
            conversation["workflow_stage"] = "collecting_slots"
            return conversation

    router_service = FakeLLMRouterService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=ActiveWorkflowConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="faq_authoritative",
        rag_service=FakeRagService(),
    )

    result = asyncio.run(service.process_event(37, make_event_with_text("ya lo mandé")))

    assert router_service.calls == []
    assert result["graph_state"]["route"] == "clarification"
    assert result["graph_state"]["llm_router_result"]["fallback_reason"] == "active_workflow_guard"
    assert result["graph_state"]["llm_router_result"]["fallback_to_deterministic"] is False
    assert result["external_commands"] == []


def test_gateway_service_router_checkpoint_metadata_preserves_router_and_rag_summary():
    service = GatewayService()
    metadata = service._build_checkpoint_success_metadata(
        {
            "route": "faq",
            "route_source": "llm_faq_authoritative",
            "rewrite_source": "llm_faq_authoritative",
            "intent_result": {"intent": "deposit_howto"},
            "llm_router_result": {
                "provider": "gemini",
                "mode": "faq_authoritative",
                "status": "accepted",
                "route": "faq",
                "intent": "deposit_howto",
                "confidence": 0.95,
                "reason": "matched FAQ",
                "rewritten_question": "怎么存款？",
                "normalized_query": "怎么存款",
                "faq_query": "怎么存款",
                "language": "zh",
                "requires_human": False,
                "requires_backend": False,
                "error_type": "RuntimeError",
                "error_message": "api_key=hidden password=hidden boom",
            },
            "rag_context": {
                "query": "怎么存款",
                "matched": True,
                "source": "knowledge_documents",
                "fallback_reason": None,
                "answer_blocks": [{"type": "text", "text": "too large"}],
                "documents": [
                    {
                        "id": 1,
                        "title": "充值方式说明",
                        "score": 12,
                        "priority": 1,
                        "matched_fields": ["question_aliases"],
                        "matched_terms": ["怎么存款"],
                        "content": "should not be copied",
                    }
                ],
            },
        }
    )

    router = metadata["llm_router"]
    assert router["reason"] == "matched FAQ"
    assert router["rewritten_question"] == "怎么存款？"
    assert router["normalized_query"] == "怎么存款"
    assert router["faq_query"] == "怎么存款"
    assert router["language"] == "zh"
    assert router["final_route"] == "faq"
    assert router["final_intent"] == "deposit_howto"
    assert router["route_source"] == "llm_faq_authoritative"
    assert router["rewrite_source"] == "llm_faq_authoritative"
    assert router["error_message"] == "api_key=[redacted] password=[redacted] boom"
    assert "hidden" not in str(router)
    assert metadata["rag"]["rag_query"] == "怎么存款"
    assert metadata["rag"]["rag_matched"] is True
    assert metadata["rag"]["rag_documents"][0]["title"] == "充值方式说明"
    assert "answer_blocks" not in str(metadata["rag"])
    assert "content" not in str(metadata["rag"])


def test_gateway_service_redacts_secret_values_from_error_metadata():
    service = GatewayService()
    error = RuntimeError("api_key=abc123 password: p@ss Bearer xyz token=tok123")

    router_state = service._router_fallback_state(
        {"route": "faq", "intent_result": {"intent": "faq_general"}},
        "exception",
        exc=error,
    )
    shadow_error = service._shadow_error_result(error)
    checkpoint = service._build_checkpoint_success_metadata(
        {
            "route": "clarification",
            "route_source": "llm_faq_authoritative",
            "rewrite_source": "llm_faq_authoritative",
            "intent_result": {"intent": "clarification_needed"},
            "llm_router_result": router_state["llm_router_result"],
            "llm_rewrite_result": shadow_error,
        }
    )

    combined = str({"router": router_state["llm_router_result"], "shadow": shadow_error, "checkpoint": checkpoint})
    assert "abc123" not in combined
    assert "p@ss" not in combined
    assert "xyz" not in combined
    assert "tok123" not in combined
    assert "api_key=[redacted]" in combined
    assert "password=[redacted]" in combined
    assert "Bearer [redacted]" in combined


def test_gateway_service_faq_authoritative_renders_multimodal_answer_blocks_to_ordered_outbox_rows():
    router_service = FakeLLMRouterService()
    rag_service = FakeRagService(
        {
            "matched": True,
            "answer": "请按以下步骤操作：",
            "answer_blocks": [
                {"type": "text", "text": "请按以下步骤操作："},
                {
                    "type": "image",
                    "asset_key": "deposit_step_1",
                    "platform_asset_map": {"JUE999": "https://cdn.example/deposit_step_1.png"},
                    "caption": "第一步：进入充值页面",
                    "position": "after",
                },
                {"type": "text", "text": "选择可用通道后提交。"},
                {"type": "buttons", "menu_key": "deposit_menu"},
            ],
            "documents": [{"id": 1, "title": "Deposit FAQ", "score": 12}],
            "fallback_reason": None,
            "source": "knowledge_documents",
            "query": "how to deposit",
            "tenant_id": "default",
            "kb_scope": "default",
        }
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="faq_authoritative",
        rag_service=rag_service,
    )

    result = asyncio.run(service.process_event(35, make_event_with_text("怎么存款？")))

    assert [message["block_index"] for message in result["outbound_messages"]] == [0, 1, 2, 3]
    assert [message["message_type"] for message in result["outbound_messages"]] == ["text", "image", "text", "buttons"]
    assert [message["command_type"] for message in result["outbound_messages"]] == [
        "livechat.send_text",
        "livechat.send_image",
        "livechat.send_text",
        "livechat.buttons_preview",
    ]
    assert result["outbound_messages"][1]["payload_json"] == {
        "asset_key": "deposit_step_1",
        "asset_ref": "https://cdn.example/deposit_step_1.png",
        "caption": "第一步：进入充值页面",
        "position": "after",
    }
    assert result["outbound_messages"][3]["payload_json"] == {"menu_key": "deposit_menu"}
    assert len({message["dedup_key"] for message in result["outbound_messages"]}) == 4


def test_gateway_service_guarded_authoritative_uses_llm_sop_route():
    router_service = FakeLLMRouterService(
        {
            "rewritten_question": "存款订单 D123456 没到账",
            "normalized_query": "存款订单 D123456 没到账",
            "language": "zh",
            "intent": "deposit_missing",
            "route": "sop",
            "confidence": 0.95,
            "sop_name": "deposit_missing",
            "faq_query": None,
            "risk_level": "elevated",
            "requires_human": False,
            "requires_backend": True,
            "missing_slots": [],
            "preserved_entities": ["D123456"],
            "reason": "requires SOP",
            "provider": "mock",
            "mode": "guarded_authoritative",
        }
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="guarded_authoritative",
    )

    result = asyncio.run(service.process_event(27, make_event_with_text("怎么存款？")))

    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_missing"
    assert result["graph_state"]["llm_router_result"]["status"] == "accepted"


def test_gateway_service_guarded_authoritative_falls_back_for_low_confidence_invalid_route_and_exception():
    for router_service, reason in [
        (FakeLLMRouterService({**FakeLLMRouterService().result, "confidence": 0.2}), "low_confidence"),
        (FakeLLMRouterService({**FakeLLMRouterService().result, "route": "bad_route"}), "validation_error"),
        (FakeLLMRouterService(error=RuntimeError("api_key=hidden")), "exception"),
    ]:
        graph_error_repository = FakeGraphRunErrorRepository()
        service = GatewayService(
            inbound_repository=FakeInboundRepository(),
            conversation_repository=FakeConversationRepository(),
            outbound_repository=FakeOutboundRepository(),
            graph_run_error_repository=graph_error_repository,
            message_repository=FakeConversationMessageRepository(),
            llm_intent_service=router_service,
            llm_router_mode="guarded_authoritative",
        )

        result = asyncio.run(service.process_event(28, make_event_with_text("how to deposit")))

        assert result["graph_state"]["route"] == "faq"
        assert result["graph_state"]["route_source"] == "deterministic"
        assert result["graph_state"]["llm_router_result"]["status"] == "fallback"
        assert result["graph_state"]["llm_router_result"]["fallback_reason"] == reason
        if reason == "exception":
            assert "api_key=[redacted]" in str(result["graph_state"]["llm_router_result"])
        assert "hidden" not in str(result["graph_state"]["llm_router_result"])
        assert graph_error_repository.inserted == []


def test_gateway_service_guarded_authoritative_hard_guards_active_workflow_human_and_backend_fact():
    class ActiveWorkflowConversationRepository(FakeConversationRepository):
        async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
            conversation = await super().get_or_create(chat_id, thread_id)
            conversation["active_workflow"] = "deposit_missing"
            conversation["workflow_stage"] = "collecting_slots"
            return conversation

    faq_router = FakeLLMRouterService()
    active_service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=ActiveWorkflowConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=faq_router,
        llm_router_mode="guarded_authoritative",
    )
    active_result = asyncio.run(active_service.process_event(29, make_event_with_text("发了")))
    assert active_result["graph_state"]["route"] == "sop"
    assert active_result["graph_state"]["llm_router_result"]["fallback_reason"] == "hard_guard"
    assert faq_router.calls == []

    human_service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=FakeLLMRouterService(),
        llm_router_mode="guarded_authoritative",
    )
    human_result = asyncio.run(human_service.process_event(30, make_event_with_text("I want human agent")))
    assert human_result["graph_state"]["route"] == "human_handoff"
    assert human_result["graph_state"]["llm_router_result"]["fallback_reason"] == "hard_guard"

    backend_service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=FakeLLMRouterService(),
        llm_router_mode="guarded_authoritative",
    )
    backend_result = asyncio.run(backend_service.process_event(31, make_event_with_text("withdrawal status and balance")))
    assert backend_result["graph_state"]["route"] != "faq"
    assert backend_result["graph_state"]["llm_router_result"]["fallback_reason"] == "hard_guard"


def test_gateway_service_deterministic_router_mode_does_not_call_llm():
    router_service = FakeLLMRouterService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="deterministic",
    )

    result = asyncio.run(service.process_event(32, make_event_with_text("how to deposit")))

    assert router_service.calls == []
    assert result["graph_state"]["route_source"] == "deterministic"
    assert result["graph_state"].get("llm_router_result") is None


def test_gateway_service_rewrite_shadow_records_result_without_overriding_deterministic_fields():
    rewrite_service = FakeLLMRewriteService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_rewrite_service=rewrite_service,
        llm_rewrite_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(17, make_event_with_text("how to deposit")))

    assert len(rewrite_service.calls) == 1
    assert result["graph_state"]["llm_rewrite_result"]["provider"] == "mock"
    assert result["graph_state"]["rewritten_question"] == "how to deposit"
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["route"] == "faq"
    assert result["external_commands"] == []
    assert result["outbound_messages"][0]["action_type"] == "send_event"


def test_gateway_service_intent_shadow_records_result_without_overriding_deterministic_route():
    intent_service = FakeLLMIntentService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=intent_service,
        llm_intent_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(18, make_event_with_text("how to deposit")))

    assert len(intent_service.calls) == 1
    assert result["graph_state"]["llm_intent_result"]["provider"] == "mock"
    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_howto"
    assert result["graph_state"]["route_source"] == "deterministic"


def test_gateway_service_sop_route_is_not_overridden_by_shadow_results():
    rewrite_service = FakeLLMRewriteService(
        {
            "rewritten_question": "how to deposit",
            "normalized_query": "how to deposit",
            "language": "en",
            "preserved_entities": [],
            "missing_or_ambiguous": [],
            "risk_flags": [],
            "confidence": 0.95,
            "reason": "force faq rewrite",
            "provider": "mock",
            "mode": "shadow",
        }
    )
    intent_service = FakeLLMIntentService(
        {
            "intent": "deposit_howto",
            "route": "faq",
            "confidence": 0.95,
            "reason": "force faq route",
            "provider": "mock",
            "mode": "shadow",
        }
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_rewrite_service=rewrite_service,
        llm_intent_service=intent_service,
        llm_rewrite_shadow_enabled=True,
        llm_intent_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(19, make_event_with_text("mi deposito no llegó")))

    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_missing"
    assert result["graph_state"]["rewritten_question"] == "mi deposito no llegó"
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["route_source"] == "deterministic"


def test_gateway_service_human_handoff_route_is_not_overridden_by_shadow_results():
    intent_service = FakeLLMIntentService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=intent_service,
        llm_intent_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(20, make_event_with_text("I want human agent")))

    assert result["graph_state"]["route"] == "human_handoff"
    assert [command["command_type"] for command in result["external_commands"]] == ["human_handoff.requested"]


def test_gateway_service_active_workflow_supplement_is_not_misrouted_by_shadow_rewrite():
    class ActiveWorkflowConversationRepository(FakeConversationRepository):
        async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
            conversation = await super().get_or_create(chat_id, thread_id)
            conversation["active_workflow"] = "deposit_missing"
            conversation["workflow_stage"] = "collecting_slots"
            return conversation

    rewrite_service = FakeLLMRewriteService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=ActiveWorkflowConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_rewrite_service=rewrite_service,
        llm_rewrite_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(21, make_event_with_text("ya lo mandé")))

    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_missing"


def test_gateway_service_backend_fact_shadow_does_not_query_knowledge_repository():
    from app.services.rag import RagService

    class FakeKnowledgeRepository:
        def __init__(self) -> None:
            self.calls = []

        async def search(self, tenant_id: str, query: str, kb_scope: str = "default", limit: int = 3):
            self.calls.append((tenant_id, query, kb_scope, limit))
            return []

    repository = FakeKnowledgeRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        rag_service=RagService(repository),
        llm_rewrite_service=FakeLLMRewriteService(),
        llm_intent_service=FakeLLMIntentService(),
        llm_rewrite_shadow_enabled=True,
        llm_intent_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(22, make_event_with_text("withdrawal status and balance")))

    assert repository.calls == []
    assert result["graph_state"]["llm_rewrite_result"] is not None
    assert result["graph_state"]["llm_intent_result"] is not None


def test_gateway_service_shadow_guardrail_failure_records_shadow_error_and_keeps_side_effects():
    inbound_repository = FakeInboundRepository()
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    graph_error_repository = FakeGraphRunErrorRepository()
    message_repository = FakeConversationMessageRepository()

    class FailingRewriteService:
        async def rewrite(self, payload: dict) -> dict:
            raise ValueError("Unsupported llm route: invalid_route")

    service = GatewayService(
        inbound_repository=inbound_repository,
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
        graph_run_error_repository=graph_error_repository,
        message_repository=message_repository,
        llm_rewrite_service=FailingRewriteService(),
        llm_rewrite_shadow_enabled=True,
        workflow_graph=RecordingGraph(),
    )

    result = asyncio.run(service.process_event(23, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["llm_rewrite_result"] == {
        "mode": "shadow",
        "status": "error",
        "error_type": "ValueError",
        "error_message": "Unsupported llm route: invalid_route",
    }
    assert inbound_repository.processed == [23]
    assert conversation_repository.updated
    assert message_repository.inserted
    assert outbound_repository.inserted
    assert external_repository.inserted == []
    assert graph_error_repository.inserted == []


def test_gateway_service_shadow_failure_does_not_block_deterministic_outbound_or_record_graph_error():
    graph_error_repository = FakeGraphRunErrorRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        graph_run_error_repository=graph_error_repository,
        message_repository=FakeConversationMessageRepository(),
        llm_rewrite_service=ExplodingLLMService(),
        llm_intent_service=ExplodingLLMService(),
        llm_rewrite_shadow_enabled=True,
        llm_intent_shadow_enabled=True,
    )

    result = asyncio.run(service.process_event(24, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "faq"
    assert result["outbound_messages"][0]["payload_json"]["text"] == result["graph_state"]["response_text"]
    assert result["outbound_messages"][0]["message_type"] == "text"
    assert result["graph_state"]["llm_rewrite_result"]["mode"] == "shadow"
    assert result["graph_state"]["llm_rewrite_result"]["status"] == "error"
    assert result["graph_state"]["llm_rewrite_result"]["error_type"] == "RuntimeError"
    assert result["graph_state"]["llm_intent_result"]["mode"] == "shadow"
    assert result["graph_state"]["llm_intent_result"]["status"] == "error"
    assert "api_key=[redacted]" in str(result["graph_state"]["llm_rewrite_result"])
    assert "password=[redacted]" in str(result["graph_state"]["llm_intent_result"])
    assert "hidden" not in str(result["graph_state"]["llm_rewrite_result"])
    assert "hidden" not in str(result["graph_state"]["llm_intent_result"])
    assert graph_error_repository.inserted == []


def test_gateway_service_checkpoint_success_metadata_contains_shadow_summary():
    checkpoint_repository = FakeCheckpointRunRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_rewrite_service=FakeLLMRewriteService(),
        llm_intent_service=FakeLLMIntentService({"intent": "human_handoff", "route": "human_handoff", "confidence": 0.91, "reason": "shadow disagreement", "provider": "mock", "mode": "shadow"}),
        llm_rewrite_shadow_enabled=True,
        llm_intent_shadow_enabled=True,
        checkpoint_run_repository=checkpoint_repository,
    )

    result = asyncio.run(service.process_event(25, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "faq"
    assert checkpoint_repository.succeeded[0][0] == 91
    metadata = checkpoint_repository.succeeded[0][2]
    assert metadata["llm_shadow"]["rewrite"]["provider"] == "mock"
    assert metadata["llm_shadow"]["rewrite"]["status"] == "ok"
    assert metadata["llm_shadow"]["intent"]["provider"] == "mock"
    assert metadata["llm_shadow"]["intent"]["route"] == "human_handoff"
    assert metadata["llm_shadow"]["deterministic_route"] == "faq"


class RecordingGraph:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, state: dict, config=None) -> dict:
        self.calls.append((state, config))
        return {
            **state,
            "response_text": "ok",
            "commands": [{"type": "livechat.send_text", "payload": {"text": "ok"}}],
        }


def test_gateway_service_invokes_graph_with_conversation_thread_config():
    graph = RecordingGraph()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=graph,
    )

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    state, config = graph.calls[0]
    assert config == {"configurable": {"thread_id": "livechat:chat-1"}}
    assert state["thread_id"] == "thread-1"
    assert result["graph_state"]["thread_id"] == "thread-1"
    assert result["outbound_message"]["payload_json"]["text"] == "ok"


def test_gateway_service_records_checkpoint_run_metadata_without_breaking_main_flow():
    checkpoint_repository = FakeCheckpointRunRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=RecordingGraph(),
        checkpoint_run_repository=checkpoint_repository,
    )

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    assert result["outbound_message"]["payload_json"]["text"] == "ok"
    assert checkpoint_repository.inserted[0]["conversation_id"] == "livechat:chat-1"
    assert checkpoint_repository.inserted[0]["checkpoint_mode"] == "off"
    assert checkpoint_repository.succeeded[0][0:2] == (91, None)


def test_gateway_service_marks_checkpoint_run_failed_when_graph_invoke_fails():
    checkpoint_repository = FakeCheckpointRunRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        graph_run_error_repository=FakeGraphRunErrorRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=ExplodingGraph(RuntimeError("graph exploded")),
        checkpoint_run_repository=checkpoint_repository,
    )

    try:
        asyncio.run(service.process_event(11, make_inbound_event()))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected graph invoke to fail")

    assert checkpoint_repository.failed == [(91, "RuntimeError", "graph exploded")]


def test_gateway_service_ignores_checkpoint_metadata_success_mark_failures():
    checkpoint_repository = FakeCheckpointRunRepository(fail_on_mark=True)
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=RecordingGraph(),
        checkpoint_run_repository=checkpoint_repository,
        checkpoint_mode="memory",
    )

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    assert result["outbound_message"]["payload_json"]["text"] == "ok"
    assert checkpoint_repository.inserted[0]["checkpoint_mode"] == "memory"


def test_gateway_service_ignores_checkpoint_metadata_failed_mark_failures_and_preserves_graph_error():
    checkpoint_repository = FakeCheckpointRunRepository(fail_on_mark_failed=True)
    graph_error_repository = FakeGraphRunErrorRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        graph_run_error_repository=graph_error_repository,
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=ExplodingGraph(RuntimeError("graph exploded")),
        checkpoint_run_repository=checkpoint_repository,
    )

    try:
        asyncio.run(service.process_event(11, make_inbound_event()))
    except RuntimeError as exc:
        assert str(exc) == "graph exploded"
    else:
        raise AssertionError("expected graph invoke to fail")

    assert graph_error_repository.inserted[0]["error_message"] == "graph exploded"


def test_gateway_service_ignores_checkpoint_metadata_insert_failures():
    checkpoint_repository = FakeCheckpointRunRepository(fail_on_insert=True)
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        workflow_graph=RecordingGraph(),
        checkpoint_run_repository=checkpoint_repository,
    )

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    assert result["outbound_message"]["payload_json"]["text"] == "ok"


def test_gateway_snapshot_sanitizes_rag_context_to_metadata_and_hides_secrets():
    service = GatewayService()

    snapshot = service._sanitize_graph_state_snapshot(
        {
            "conversation_id": "livechat:chat-1",
            "tenant_id": "default",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "raw_user_input": "bonus rules",
            "event_type": "MESSAGE_CREATED",
            "slot_memory": {"password": "secret-password", "safe": "ok"},
            "llm_rewrite_result": {"provider": "mock", "rewritten_question": "shadow", "api_key": "skip-me"},
            "llm_intent_result": {"provider": "mock", "route": "faq", "secret": "skip-me-too"},
            "rag_context": {
                "matched": True,
                "answer": "x" * 2500,
                "documents": [
                    {
                        "id": 1,
                        "title": "Bonus rules",
                        "content": "y" * 5000,
                        "matched_fields": ["title"],
                    }
                ],
            },
        }
    )

    assert snapshot["slot_memory"] == {"safe": "ok"}
    assert snapshot["llm_rewrite_result"] == {"provider": "mock", "rewritten_question": "shadow"}
    assert snapshot["llm_intent_result"] == {"provider": "mock", "route": "faq"}
    assert snapshot["rag_context"]["matched"] is True
    assert snapshot["rag_context"]["documents"][0]["title"] == "Bonus rules"
    assert "content" not in snapshot["rag_context"]["documents"][0]
    assert len(snapshot["rag_context"]["answer"]) == 2000


class FakeTransactionalGatewayRepository:
    def __init__(self, fail_on_outbox: bool = False) -> None:
        self.fail_on_outbox = fail_on_outbox
        self.calls = []
        self.committed = False
        self.rolled_back = False
        self.conversation_repository = FakeConversationRepository()
        self.message_repository = FakeConversationMessageRepository()

    async def process_event_transactionally(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        customer_message: dict | None,
        outbound_messages: list[dict],
        external_commands: list[dict],
        graph_state: dict | None = None,
    ) -> dict:
        self.calls.append((inbound_event_id, event, customer_message, outbound_messages, external_commands, graph_state))
        try:
            if self.fail_on_outbox:
                raise RuntimeError("outbox insert failed")
            self.committed = True
            return {
                "conversation": {"conversation_id": f"livechat:{event.chat_id}"},
                "outbound_insert": {"inserted": bool(outbound_messages), "duplicate": False, "id": 1 if outbound_messages else None},
                "outbound_inserts": [
                    {"inserted": True, "duplicate": False, "id": index + 1}
                    for index, _message in enumerate(outbound_messages)
                ],
                "external_command_inserts": [
                    {"inserted": True, "duplicate": False, "id": index + 1}
                    for index, _command in enumerate(external_commands)
                ],
            }
        except Exception:
            self.rolled_back = True
            raise


def test_gateway_service_uses_transactional_repository_for_state_outbox_and_processed():
    transactional = FakeTransactionalGatewayRepository()
    service = GatewayService(transactional_repository=transactional)

    result = asyncio.run(service.process_event(11, make_inbound_event()))

    assert transactional.committed is True
    assert transactional.rolled_back is False
    assert transactional.calls[0][0] == 11
    assert transactional.calls[0][2]["inbound_event_id"] == 11
    assert transactional.calls[0][3][0]["inbound_event_id"] == 11
    assert transactional.calls[0][5]["intent_result"]["intent"] == "deposit_missing"
    assert result["outbound_insert"] == {"inserted": True, "duplicate": False, "id": 1}


def test_gateway_service_file_received_updates_slot_memory():
    conversation_repository = FakeConversationRepository()
    conversation_repository.get_or_create = async_get_or_create_with_active_deposit
    outbound_repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        message_repository=message_repository,
    )
    event = make_inbound_event()
    event.standard_event_type = "FILE_RECEIVED"
    event.event_type = "file"
    event.payload_json = {
        "event": {
            "type": "file",
            "url": "https://cdn.example/deposit.png",
            "name": "deposit.png",
        }
    }

    result = asyncio.run(service.process_event(12, event))

    assert result["graph_state"]["slot_memory"]["deposit_screenshot"] == "https://cdn.example/deposit.png"
    assert result["graph_state"]["active_workflow"] == "deposit_missing"
    assert message_repository.inserted[0]["message_type"] == "file"
    assert message_repository.inserted[0]["attachment_refs"][0]["url"] == "https://cdn.example/deposit.png"


async def async_get_or_create_with_active_deposit(chat_id: str, thread_id: str | None = None) -> dict:
    return {
        "conversation_id": f"livechat:{chat_id}",
        "tenant_id": "default",
        "channel_type": "livechat",
        "chat_id": chat_id,
        "current_thread_id": thread_id,
        "status": "AI_ACTIVE",
        "active_workflow": "deposit_missing",
        "workflow_stage": "collecting_slots",
        "slot_memory": {"account_or_phone": "andy123"},
    }


def test_gateway_service_rolls_back_when_transactional_processing_fails():
    transactional = FakeTransactionalGatewayRepository(fail_on_outbox=True)
    service = GatewayService(transactional_repository=transactional)

    try:
        asyncio.run(service.process_event(11, make_inbound_event()))
    except RuntimeError as exc:
        assert str(exc) == "outbox insert failed"
    else:
        raise AssertionError("expected transactional processing to fail")

    assert transactional.committed is False
    assert transactional.rolled_back is True


class ExplodingGraph:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = []

    def invoke(self, state: dict, config=None) -> dict:
        self.calls.append((state, config))
        raise self.error


def test_gateway_service_does_not_persist_side_effects_when_graph_invoke_fails():
    inbound_repository = FakeInboundRepository()
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    graph_error_repository = FakeGraphRunErrorRepository()
    message_repository = FakeConversationMessageRepository()
    service = GatewayService(
        inbound_repository=inbound_repository,
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
        graph_run_error_repository=graph_error_repository,
        message_repository=message_repository,
        workflow_graph=ExplodingGraph(RuntimeError("graph exploded")),
    )

    try:
        asyncio.run(service.process_event(11, make_inbound_event()))
    except RuntimeError as exc:
        assert str(exc) == "graph exploded"
    else:
        raise AssertionError("expected graph invoke to fail")

    assert inbound_repository.processed == []
    assert conversation_repository.updated == []
    assert message_repository.inserted == []
    assert outbound_repository.inserted == []
    assert external_repository.inserted == []
    assert graph_error_repository.inserted[0]["conversation_id"] == "livechat:chat-1"
    assert graph_error_repository.inserted[0]["inbound_event_id"] == 11
    assert graph_error_repository.inserted[0]["error_type"] == "RuntimeError"
    assert graph_error_repository.inserted[0]["error_message"] == "graph exploded"
    assert graph_error_repository.inserted[0]["retryable"] == 0
    assert graph_error_repository.inserted[0]["graph_thread_id"] == "livechat:chat-1"
    assert graph_error_repository.inserted[0]["state_snapshot"]["conversation_id"] == "livechat:chat-1"
    assert graph_error_repository.inserted[0]["state_snapshot"]["raw_user_input"] == "mi deposito no llegó"


def test_gateway_service_marks_timeout_errors_retryable():
    graph_error_repository = FakeGraphRunErrorRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        graph_run_error_repository=graph_error_repository,
        workflow_graph=ExplodingGraph(TimeoutError("graph timeout")),
    )

    try:
        asyncio.run(service.process_event(11, make_inbound_event()))
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected graph invoke to fail")

    assert graph_error_repository.inserted[0]["retryable"] == 1
