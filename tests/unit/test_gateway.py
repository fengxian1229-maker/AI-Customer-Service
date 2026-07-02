import asyncio

import pytest

from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService, build_fixed_reply, should_enqueue_reply
from app.workflows.command_contracts import CommandType


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


def make_event_with_button(button_id: str, text: str = "") -> InboundEvent:
    event = make_inbound_event()
    event.payload_json = {"event": {"type": "message", "text": text, "postback_id": button_id}}
    return event


def test_should_enqueue_reply_for_message_created():
    assert should_enqueue_reply(make_inbound_event()) is True


def test_build_fixed_reply_message():
    outbox = build_fixed_reply(make_inbound_event())

    assert outbox["action_type"] == "send_event"
    assert outbox["payload_json"]["text"] == "Hello, I received your message. How can I help you today?"


class FakeConversationRepository:
    def __init__(self, status: str = "AI_ACTIVE") -> None:
        self.calls = []
        self.updated = []
        self.status = status

    async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
        self.calls.append((chat_id, thread_id))
        return {
            "conversation_id": f"livechat:{chat_id}",
            "tenant_id": "default",
            "channel_type": "livechat",
            "chat_id": chat_id,
            "current_thread_id": thread_id,
            "status": self.status,
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
                    "title": "充值教程",
                    "content": "按页面提示完成充值。",
                    "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
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


class FakeLLMSopSlotService:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {
            "intent": "deposit_missing",
            "extracted_slots": {"account_or_phone": "andy123", "amount": "500"},
            "attachment_classification": {},
            "missing_slots": ["deposit_screenshot"],
            "confidence": {"account_or_phone": 0.9, "amount": 0.8},
            "reason": "extracted slots",
            "provider": "mock",
            "mode": "sop_slot",
        }
        self.error = error
        self.calls = []

    async def extract_sop_slots(self, payload: dict) -> dict:
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.result


class FakeFinalReplyService:
    def __init__(self, final_text: str = "final composed text") -> None:
        self.final_text = final_text
        self.calls = []

    async def compose(self, graph_state: dict) -> dict:
        self.calls.append(graph_state)
        return {
            **graph_state,
            "response_text_fallback": graph_state.get("response_text"),
            "final_response_text": self.final_text,
            "final_reply_result": {"status": "accepted", "confidence": 0.91},
        }


class FakeBlockFinalReplyService:
    def __init__(self, translations: dict[str, str] | None = None, error_on: str | None = None) -> None:
        self.translations = translations or {}
        self.error_on = error_on
        self.calls = []

    async def compose(self, graph_state: dict) -> dict:
        self.calls.append(graph_state)
        fallback = graph_state["response_text_fallback"]
        if self.error_on and self.error_on in fallback:
            return {
                **graph_state,
                "final_response_text": fallback,
                "final_reply_result": {"status": "fallback", "fallback_reason": "test_error"},
            }
        return {
            **graph_state,
            "final_response_text": self.translations.get(fallback, fallback),
            "final_reply_result": {"status": "accepted", "confidence": 0.9},
        }


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


def test_gateway_livechat_nav_button_sends_submenu_without_llm_router():
    router_service = FakeLLMRouterService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
    )

    result = asyncio.run(service.process_event(41, make_event_with_button("deposit_menu", "💰 Problemas de depósito")))

    assert router_service.calls == []
    assert result["graph_state"]["route"] == "final_reply"
    assert result["graph_state"]["slot_memory"]["livechat_menu"]["context"] == "deposit"
    assert result["outbound_messages"][0]["message_type"] == "buttons"
    assert result["outbound_messages"][0]["payload_json"]["menu_key"] == "deposit"


def test_gateway_livechat_business_button_routes_to_deposit_sop():
    router_service = FakeLLMRouterService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
    )

    result = asyncio.run(service.process_event(42, make_event_with_button("main_deposito", "🧾 Depósito no acreditado")))

    assert router_service.calls == []
    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_missing"
    assert result["graph_state"]["active_workflow"] == "deposit_missing"


def test_gateway_livechat_faq_button_routes_to_faq():
    rag_service = FakeRagService(
        {
            "matched": True,
            "answer": "FAQ answer",
            "answer_blocks": [{"type": "text", "text": "FAQ answer"}],
            "documents": [],
            "fallback_reason": None,
            "source": "knowledge_documents",
        }
    )
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        rag_service=rag_service,
    )

    result = asyncio.run(service.process_event(43, make_event_with_button("deposit_howto", "📘 Cómo recargar")))

    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_howto"
    assert result["outbound_messages"][0]["payload_json"]["text"] == "FAQ answer"


def test_gateway_livechat_human_button_routes_to_handoff():
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
    )

    result = asyncio.run(service.process_event(44, make_event_with_button("global_human", "👤 Atención humana")))

    assert result["graph_state"]["route"] == "human_handoff"
    assert [command["command_type"] for command in result["external_commands"]] == ["human_handoff.requested"]


def test_gateway_livechat_real_new_conversation_appends_main_menu_once():
    conversation_repository = FakeConversationRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
    )
    event = make_event_with_text("mi deposito no llegó")
    event.payload_json["ingress_source"] = "polling"

    result = asyncio.run(service.process_event(45, event))

    assert [message["message_type"] for message in result["outbound_messages"]] == ["text", "buttons"]
    assert result["outbound_messages"][1]["payload_json"]["menu_key"] == "main"
    assert conversation_repository.updated[0][1]["slot_memory"]["livechat_menu"]["intro_sent"] is True


def test_gateway_livechat_handoff_does_not_append_intro_menu():
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
    )
    event = make_event_with_text("I want human agent")
    event.payload_json["ingress_source"] = "polling"

    result = asyncio.run(service.process_event(46, event))

    assert [message["message_type"] for message in result["outbound_messages"]] == ["text"]


def test_gateway_service_uses_final_reply_text_for_outbound_message():
    final_reply_service = FakeFinalReplyService("您好，请提供用户名或注册手机号，并上传存款付款截图。")
    outbound_repository = FakeOutboundRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=outbound_repository,
        message_repository=FakeConversationMessageRepository(),
        llm_final_reply_service=final_reply_service,
        llm_final_reply_enabled=True,
    )

    result = asyncio.run(service.process_event(12, make_inbound_event()))

    assert final_reply_service.calls
    assert result["graph_state"]["response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["graph_state"]["final_response_text"] == "您好，请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["outbound_messages"][0]["payload_json"]["text"] == "您好，请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["graph_state"]["commands"][0]["payload"]["text"] == "您好，请提供用户名或注册手机号，并上传存款付款截图。"


def test_gateway_language_policy_file_received_preserves_last_user_language():
    class FileConversationRepository(FakeConversationRepository):
        async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
            conversation = await super().get_or_create(chat_id, thread_id)
            conversation["slot_memory"] = {"last_user_language": "tl"}
            conversation["active_workflow"] = "deposit_missing"
            conversation["workflow_stage"] = "waiting_backend"
            return conversation

    event = make_inbound_event()
    event.standard_event_type = "FILE_RECEIVED"
    event.event_type = "file"
    event.payload_json = {"event": {"type": "file", "url": "https://cdn.example/proof.png"}}
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FileConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
    )

    result = asyncio.run(service.process_event(17, event))

    assert result["graph_state"]["detected_language"] == "unknown"
    assert result["graph_state"]["reply_language"] == "tl"
    assert result["graph_state"]["slot_memory"]["last_user_language"] == "tl"
    assert result["graph_state"]["slot_memory"]["last_reply_language"] == "tl"


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
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "感谢您提供的截图，我们现在为您查询，请稍等。"
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
    event.payload_json = {"event": {"type": "message", "text": "how to deposit"}}

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
    assert result["outbound_messages"][0]["payload_json"] == {
        "type": "message",
        "text": "我会为你转接真人客服继续协助。",
        "handoff_ack": True,
    }
    assert outbound_repository.inserted[0]["payload_json"] == result["outbound_messages"][0]["payload_json"]


def test_gateway_service_human_handoff_outbound_uses_final_reply_and_preserves_ack_marker():
    final_text = "非常抱歉给您带来困扰，我会为您记录您的意向并将您的需求转接真人客服。"
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_final_reply_service=FakeFinalReplyService(final_text),
        llm_final_reply_enabled=True,
    )

    result = asyncio.run(service.process_event(14, make_event_with_text("转人工客服")))

    assert result["graph_state"]["route"] == "human_handoff"
    assert result["graph_state"]["final_response_text"] == final_text
    assert result["outbound_messages"][0]["payload_json"] == {
        "type": "message",
        "text": final_text,
        "handoff_ack": True,
    }
    assert outbound_repository.inserted[0]["payload_json"]["handoff_ack"] is True


def test_gateway_service_withdrawal_blocked_generates_backend_query_without_rag_or_telegram():
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

    result = asyncio.run(service.process_event(88, make_event_with_text("提款不了，提示还有流水，用户名 andy123")))

    assert rag_service.calls == []
    assert result["graph_state"]["intent_result"]["intent"] == "withdrawal_blocked_or_rollover"
    assert result["graph_state"]["workflow_stage"] == "backend_querying"
    assert result["outbound_messages"] == []
    assert outbound_repository.inserted == []
    assert result["graph_state"].get("response_text") is None
    assert result["graph_state"].get("response_text_fallback") is None
    assert result["graph_state"]["customer_reply"]["intent"] == "backend_query_waiting"
    assert [command["command_type"] for command in external_repository.inserted] == ["backend.query"]
    assert external_repository.inserted[0]["payload_json"]["intent"] == "withdrawal_blocked_or_rollover"
    assert external_repository.inserted[0]["payload_json"]["account_or_phone"] == "andy123"
    assert external_repository.inserted[0]["payload_json"]["reply_language"] == result["graph_state"]["reply_language"]
    assert external_repository.inserted[0]["payload_json"]["conversation_language"] == result["graph_state"]["conversation_language"]
    assert external_repository.inserted[0]["payload_json"]["detected_language"] == result["graph_state"]["detected_language"]


def test_gateway_service_human_active_records_inbound_but_does_not_run_graph_or_enqueue_work():
    conversation_repository = FakeConversationRepository(status="HUMAN_ACTIVE")
    outbound_repository = FakeOutboundRepository()
    external_repository = FakeExternalCommandRepository()
    message_repository = FakeConversationMessageRepository()
    graph = RecordingGraph()
    inbound_repository = FakeInboundRepository()
    service = GatewayService(
        inbound_repository=inbound_repository,
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        external_command_repository=external_repository,
        message_repository=message_repository,
        workflow_graph=graph,
    )

    result = asyncio.run(service.process_event(15, make_event_with_text("are you there?")))

    assert graph.calls == []
    assert result["graph_state"] is None
    assert result["outbound_messages"] == []
    assert result["external_commands"] == []
    assert outbound_repository.inserted == []
    assert external_repository.inserted == []
    assert message_repository.inserted[0]["sender_role"] == "customer"
    assert inbound_repository.processed == [15]


def test_gateway_service_does_not_prefetch_rag_context_before_graph_for_faq_route():
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
    assert rag_service.calls == []
    assert state.get("rag_context") is None


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
    from app.services.rag import RagService

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
    assert state.get("rag_context") is None
    assert result["graph_state"]["response_text"] == "ok"


def test_gateway_service_does_not_call_rag_before_graph_when_rag_service_would_fail():
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

    result = asyncio.run(service.process_event(15, make_event_with_text("how to deposit")))

    assert graph.calls
    assert result["graph_state"]["response_text"] == "ok"
    assert inbound_repository.processed == [15]
    assert conversation_repository.updated
    assert message_repository.inserted
    assert outbound_repository.inserted
    assert external_repository.inserted == []
    assert graph_error_repository.inserted == []
    assert service.rag_service.calls == []


def test_gateway_service_without_rag_service_returns_no_match_for_faq_static_fallback():
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


def test_gateway_service_guarded_authoritative_calls_router_inside_graph_for_sop_like_text():
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

    assert len(router_service.calls) == 1
    assert router_service.calls[0]["rewritten_question"] == "mi deposito no llegó"
    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_howto"
    assert result["graph_state"]["llm_router_result"]["status"] == "accepted"
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
            "intent": "deposit_howto",
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
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["intent_result"]["faq_query"] == "deposit not arrived FAQ"
    assert rag_service.calls[0]["intent_result"]["faq_query"] == "deposit not arrived FAQ"
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

    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_howto"
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"


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

    assert len(router_service.calls) == 1
    assert router_service.calls[0]["active_workflow"] == "deposit_missing"
    assert result["graph_state"]["route"] == "sop"
    assert result["graph_state"]["intent_result"]["workflow_relation"] == "current_workflow_supplement"


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
    assert router["faq_query"] == "怎么存款"
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
    error = RuntimeError("api-key=\"abc123\" x-api-key: xyz Authorization: Bearer qqq password='p@ss'")

    router_state = service._router_fallback_state(
        {"route": "faq", "intent_result": {"intent": "faq_general"}},
        "exception",
        exc=error,
    )
    shadow_error = service._shadow_error_result(error)
    checkpoint = service._build_checkpoint_success_metadata(
        {
            "route": "final_reply",
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
    assert " xyz" not in combined
    assert "qqq" not in combined
    assert "api-key=[redacted]" in combined
    assert "x-api-key=[redacted]" in combined
    assert "Authorization: Bearer [redacted]" in combined
    assert "password=[redacted]" in combined


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
        "livechat.send_buttons",
    ]
    assert result["outbound_messages"][1]["payload_json"] == {
        "asset_key": "deposit_step_1",
        "asset_ref": "https://cdn.example/deposit_step_1.png",
        "caption": "第一步：进入充值页面",
        "position": "after",
    }
    assert result["outbound_messages"][3]["payload_json"] == {"menu_key": "deposit_menu"}
    assert len({message["dedup_key"] for message in result["outbound_messages"]}) == 4


def test_gateway_faq_multiblock_uses_reply_language_for_outbound_plan(monkeypatch):
    import app.services.gateway as gateway_module

    captured = {}

    def fake_build_plan_from_rag_context(rag_context, **kwargs):
        captured["language"] = kwargs["language"]
        return {
            "source": "faq_answer_blocks",
            "dry_run": True,
            "message_count": 1,
            "messages": [
                {
                    "block_index": 0,
                    "message_kind": "text",
                    "command_type": "livechat.send_text",
                    "dry_run": True,
                    "dedup_key": "dedup",
                    "payload": {"text": "FAQ text"},
                    "warnings": [],
                }
            ],
        }

    monkeypatch.setattr(gateway_module, "build_faq_outbound_plan_from_rag_context", fake_build_plan_from_rag_context)
    service = GatewayService()
    event = make_inbound_event()

    rows = asyncio.run(
        service._build_outbound_messages(
            77,
            event,
            "livechat:chat-1",
            {
                "tenant_id": "default",
                "channel_type": "livechat",
                "route": "faq",
                "reply_language": "tl",
                "rewrite_result": {"language": "zh-Hans"},
                "rag_context": {"answer_blocks": [{"type": "text", "text": "FAQ text"}]},
                "commands": [],
            },
        )
    )

    assert captured["language"] == "tl"
    assert rows[0]["payload_json"]["text"] == "FAQ text"


def test_gateway_faq_multiblock_finalizes_each_text_block_and_preserves_media():
    final_reply_service = FakeBlockFinalReplyService(
        {
            "第一段中文": "First English block",
            "第二段中文": "Second English block",
        }
    )
    service = GatewayService(llm_final_reply_service=final_reply_service, llm_final_reply_enabled=True)
    event = make_inbound_event()

    rows = asyncio.run(
        service._build_outbound_messages(
            77,
            event,
            "livechat:chat-1",
            {
                "tenant_id": "default",
                "channel_type": "livechat",
                "conversation_id": "livechat:chat-1",
                "raw_user_input": "How do I withdraw?",
                "route": "faq",
                "reply_language": "en",
                "rag_context": {
                    "answer_blocks": [
                        {"type": "text", "text": "第一段中文"},
                        {
                            "type": "image",
                            "asset_key": "withdrawal_howto",
                            "platform_asset_map": {"JUE999": "https://cdn.example/withdrawal.png"},
                            "caption": "提款教程",
                            "position": "before",
                        },
                        {"type": "text", "text": "第二段中文"},
                        {"type": "buttons", "menu_key": "withdrawal_recovery"},
                    ]
                },
                "commands": [],
            },
        )
    )

    assert [row["message_type"] for row in rows] == ["text", "image", "text", "buttons"]
    assert rows[0]["payload_json"]["text"] == "First English block"
    assert rows[1]["payload_json"]["asset_ref"] == "https://cdn.example/withdrawal.png"
    assert rows[2]["payload_json"]["text"] == "Second English block"
    assert rows[3]["payload_json"] == {"menu_key": "withdrawal_recovery"}
    assert [call["response_text_fallback"] for call in final_reply_service.calls] == ["第一段中文", "第二段中文"]
    assert all(call["reply_language"] == "en" for call in final_reply_service.calls)


def test_gateway_faq_multiblock_text_finalization_falls_back_per_block():
    final_reply_service = FakeBlockFinalReplyService({"第一段中文": "First English block"}, error_on="第二段")
    service = GatewayService(llm_final_reply_service=final_reply_service, llm_final_reply_enabled=True)
    event = make_inbound_event()

    rows = asyncio.run(
        service._build_outbound_messages(
            77,
            event,
            "livechat:chat-1",
            {
                "tenant_id": "default",
                "channel_type": "livechat",
                "route": "faq",
                "reply_language": "en",
                "rag_context": {
                    "answer_blocks": [
                        {"type": "text", "text": "第一段中文"},
                        {"type": "text", "text": "第二段中文"},
                    ]
                },
                "commands": [],
            },
        )
    )

    assert rows[0]["payload_json"]["text"] == "First English block"
    assert rows[1]["payload_json"]["text"] == "第二段中文"
    assert len(final_reply_service.calls) == 2


def test_gateway_allows_handoff_route_only_for_handoff_ack_command():
    service = GatewayService()
    event = make_inbound_event()

    rows = asyncio.run(
        service._build_outbound_messages(
            77,
            event,
            "livechat:chat-1",
            {
                "route": "human_handoff",
                "final_response_text": "我会为你转接真人客服继续协助。",
                "commands": [
                    {
                        "type": CommandType.LIVECHAT_SEND_TEXT,
                        "payload": {"text": "fallback", "handoff_ack": True},
                    }
                ],
            },
        )
    )

    assert len(rows) == 1
    assert rows[0]["payload_json"]["text"] == "我会为你转接真人客服继续协助。"
    assert rows[0]["payload_json"]["handoff_ack"] is True


def test_gateway_blocks_handoff_route_without_handoff_ack_command():
    service = GatewayService()
    event = make_inbound_event()

    rows = asyncio.run(
        service._build_outbound_messages(
            77,
            event,
            "livechat:chat-1",
            {
                "route": "human_handoff",
                "commands": [
                    {
                        "type": CommandType.LIVECHAT_SEND_TEXT,
                        "payload": {"text": "ordinary bot reply"},
                    }
                ],
            },
        )
    )

    assert rows == []


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


def test_gateway_service_llm_sop_slot_extractor_merges_slots_before_sop():
    slot_service = FakeLLMSopSlotService()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_sop_slot_service=slot_service,
        llm_sop_slot_enabled=True,
        llm_sop_slot_min_confidence=0.7,
    )

    result = asyncio.run(service.process_event(64, make_event_with_text("mi deposito no llegó usuario andy123 monto 500")))

    assert slot_service.calls
    assert result["graph_state"]["sop_slot_source"] == "llm_guarded"
    assert result["graph_state"]["llm_sop_slot_result"]["status"] == "accepted"
    assert result["graph_state"]["slot_memory"]["account_or_phone"] == "andy123"
    assert result["graph_state"]["slot_memory"]["amount"] == "500"


def test_gateway_service_llm_sop_slot_extractor_invalid_attachment_falls_back():
    slot_service = FakeLLMSopSlotService(
        {
            "intent": "deposit_missing",
            "extracted_slots": {"account_or_phone": "andy123", "deposit_screenshot": "https://evil.example/x.png"},
            "attachment_classification": {"deposit_screenshot": "https://evil.example/x.png"},
            "missing_slots": [],
            "confidence": {"account_or_phone": 0.9, "deposit_screenshot": 0.9},
            "reason": "bad attachment",
        }
    )
    event = make_event_with_text("mi deposito no llegó usuario andy123")
    event.payload_json["attachments"] = [{"url": "https://cdn.example/allowed.png"}]
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_sop_slot_service=slot_service,
        llm_sop_slot_enabled=True,
    )

    result = asyncio.run(service.process_event(65, event))

    assert result["graph_state"]["llm_sop_slot_result"]["status"] == "fallback"
    assert result["graph_state"]["slot_memory"]["deposit_screenshot"] == "https://cdn.example/allowed.png"


def test_gateway_service_llm_sop_slot_extractor_exception_fallback_does_not_block_gateway():
    slot_service = FakeLLMSopSlotService(error=RuntimeError("slot failed with api_key=hidden"))
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_sop_slot_service=slot_service,
        llm_sop_slot_enabled=True,
    )

    result = asyncio.run(service.process_event(66, make_event_with_text("mi deposito no llegó usuario andy123")))

    assert result["graph_state"]["llm_sop_slot_result"]["status"] == "fallback"
    assert "hidden" not in str(result["graph_state"]["llm_sop_slot_result"])


def test_gateway_llm_sop_slot_input_prefers_reply_language():
    service = GatewayService()

    payload = service._build_llm_sop_slot_input(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {},
            "raw_user_input": "",
            "rewritten_question": "",
            "attachments": [],
            "recent_messages": [],
            "reply_language": "tl",
            "rewrite_result": {"language": "unknown"},
        }
    )

    assert payload["language"] == "tl"


def test_gateway_service_guarded_authoritative_uses_llm_human_handoff_route():
    router_service = FakeLLMRouterService(
        {
            "rewritten_question": "I need a real support agent",
            "normalized_query": "I need a real support agent",
            "language": "en",
            "intent": "human_handoff_request",
            "route": "human_handoff",
            "confidence": 0.96,
            "sop_name": None,
            "faq_query": None,
            "risk_level": "elevated",
            "requires_human": True,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "requires human",
            "provider": "mock",
            "mode": "guarded_authoritative",
        }
    )
    external_repository = FakeExternalCommandRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=external_repository,
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="guarded_authoritative",
    )

    result = asyncio.run(service.process_event(38, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "human_handoff"
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"
    assert result["graph_state"]["intent_result"]["intent"] == "explicit_human_request"
    assert result["graph_state"]["llm_router_result"]["status"] == "accepted"
    assert [command["command_type"] for command in result["external_commands"]] == ["human_handoff.requested"]
    assert [command["command_type"] for command in external_repository.inserted] == ["human_handoff.requested"]


def test_gateway_service_guarded_authoritative_forces_requires_human_for_llm_handoff_route():
    router_service = FakeLLMRouterService(
        {
            "rewritten_question": "I need a real support agent",
            "normalized_query": "I need a real support agent",
            "language": "en",
            "intent": "explicit_human_request",
            "route": "human_handoff",
            "confidence": 0.96,
            "sop_name": None,
            "faq_query": None,
            "risk_level": "elevated",
            "requires_human": False,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "requires human",
            "provider": "mock",
            "mode": "guarded_authoritative",
        }
    )
    external_repository = FakeExternalCommandRepository()
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=external_repository,
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="guarded_authoritative",
    )

    result = asyncio.run(service.process_event(40, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "human_handoff"
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"
    assert result["graph_state"]["intent_result"]["intent"] == "explicit_human_request"
    assert result["graph_state"]["llm_router_result"]["status"] == "accepted"
    assert result["graph_state"]["llm_router_result"]["requires_human"] is True
    assert [command["command_type"] for command in result["external_commands"]] == ["human_handoff.requested"]
    assert [command["command_type"] for command in external_repository.inserted] == ["human_handoff.requested"]
    assert result["outbound_messages"][0]["payload_json"] == {
        "type": "message",
        "text": "我会为你转接真人客服继续协助。",
        "handoff_ack": True,
    }


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


def test_gateway_service_guarded_authoritative_fallback_disabled_does_not_use_deterministic_faq():
    router_service = FakeLLMRouterService({**FakeLLMRouterService().result, "route": "bad_route"})
    service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        external_command_repository=FakeExternalCommandRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=router_service,
        llm_router_mode="guarded_authoritative",
        llm_router_fallback_to_deterministic=False,
    )

    result = asyncio.run(service.process_event(39, make_event_with_text("how to deposit")))

    assert result["graph_state"]["route"] == "final_reply"
    assert result["graph_state"]["route_source"] == "llm_guarded_authoritative"
    assert result["graph_state"]["intent_result"]["intent"] == "clarification_needed"
    assert result["graph_state"]["llm_router_result"]["status"] == "fallback"
    assert result["graph_state"]["llm_router_result"]["fallback_reason"] == "validation_error"
    assert result["graph_state"]["llm_router_result"]["fallback_to_deterministic"] is False
    assert result["external_commands"] == []


def test_gateway_service_guarded_authoritative_passes_guard_context_to_graph_router():
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
    assert active_result["graph_state"]["route"] == "final_reply"
    assert active_result["graph_state"]["route_source"] == "deterministic"
    assert len(faq_router.calls) == 1
    assert faq_router.calls[0]["active_workflow"] == "deposit_missing"

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
    assert human_result["graph_state"]["route_source"] == "deterministic"

    backend_service = GatewayService(
        inbound_repository=FakeInboundRepository(),
        conversation_repository=FakeConversationRepository(),
        outbound_repository=FakeOutboundRepository(),
        message_repository=FakeConversationMessageRepository(),
        llm_intent_service=FakeLLMRouterService(),
        llm_router_mode="guarded_authoritative",
    )
    backend_result = asyncio.run(backend_service.process_event(31, make_event_with_text("withdrawal status and balance")))
    assert backend_result["graph_state"]["route"] == "faq"
    assert backend_result["graph_state"]["route_source"] == "llm_guarded_authoritative"


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
    assert result["graph_state"].get("llm_router_result", {}).get("fallback_reason") == "missing_provider"


def test_gateway_service_rewrite_shadow_flag_does_not_call_gateway_level_llm():
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

    assert rewrite_service.calls == []
    assert result["graph_state"]["llm_rewrite_result"] is None
    assert result["graph_state"]["rewritten_question"] == "how to deposit"
    assert result["graph_state"]["rewrite_source"] == "deterministic"
    assert result["graph_state"]["route"] == "faq"
    assert result["external_commands"] == []
    assert result["outbound_messages"][0]["action_type"] == "send_event"


def test_gateway_service_intent_shadow_flag_does_not_call_gateway_level_llm():
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

    assert intent_service.calls == []
    assert result["graph_state"]["llm_intent_result"] is None
    assert result["graph_state"]["route"] == "faq"
    assert result["graph_state"]["intent_result"]["intent"] == "deposit_howto"
    assert result["graph_state"]["route_source"] == "deterministic"


def test_gateway_service_sop_route_is_not_overridden_by_legacy_shadow_flags():
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

    assert rewrite_service.calls == []
    assert intent_service.calls == []
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
    assert result["graph_state"]["llm_rewrite_result"] is None
    assert result["graph_state"]["llm_intent_result"] is None


def test_gateway_service_shadow_flag_does_not_run_when_custom_graph_is_injected():
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

    assert result["graph_state"]["response_text"] == "ok"
    assert result["graph_state"].get("llm_rewrite_result") is None
    assert inbound_repository.processed == [23]
    assert conversation_repository.updated
    assert message_repository.inserted
    assert outbound_repository.inserted
    assert external_repository.inserted == []
    assert graph_error_repository.inserted == []


def test_gateway_service_legacy_shadow_failure_does_not_run_or_block_deterministic_outbound():
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
    assert result["graph_state"]["llm_rewrite_result"] is None
    assert result["graph_state"]["llm_intent_result"] is None
    assert graph_error_repository.inserted == []


def test_gateway_service_checkpoint_success_metadata_omits_legacy_shadow_when_not_run():
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
    assert "llm_shadow" not in metadata


class RecordingGraph:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, state: dict, config=None) -> dict:
        raise AssertionError("GatewayService must call workflow_graph.ainvoke")

    async def ainvoke(self, state: dict, config=None) -> dict:
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
        raise AssertionError("GatewayService must call workflow_graph.ainvoke")

    async def ainvoke(self, state: dict, config=None) -> dict:
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
