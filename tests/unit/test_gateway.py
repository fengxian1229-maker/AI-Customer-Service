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
        payload_json={"event": {"type": "message", "text": "mi deposito no llegó"}},
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
