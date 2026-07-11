import asyncio
from pathlib import Path


class FakeInboundRepository:
    def __init__(self) -> None:
        self.events = []
        self.dedup_keys = set()

    async def insert(self, event):
        if event.dedup_key in self.dedup_keys:
            return {"inserted": False, "duplicate": True}
        self.dedup_keys.add(event.dedup_key)
        self.events.append(event)
        return {"inserted": True, "duplicate": False}


class FakeLiveChatClient:
    def __init__(self, listed: list[dict], details: dict[str, dict]) -> None:
        self.listed = listed
        self.details = details
        self.list_calls = 0
        self.get_calls = []

    async def list_chats(self, limit: int = 20) -> list[dict]:
        self.list_calls += 1
        return self.listed[:limit]

    async def get_chat(self, chat_id: str) -> dict:
        self.get_calls.append(chat_id)
        return self.details[chat_id]


def make_chat(
    chat_id: str = "chat-1",
    group_id: int = 23,
    author_id: str = "customer-1",
    author_type: str = "customer",
    event_id: str = "event-1",
) -> dict:
    return {
        "id": chat_id,
        "access": {"group_ids": [group_id]},
        "users": [
            {"id": "customer-1", "type": "customer"},
            {"id": "agent-1", "type": "agent"},
            {"id": "self-agent", "type": "agent"},
        ],
        "threads": [
            {
                "id": "thread-1",
                "events": [
                    {
                        "id": event_id,
                        "type": "message",
                        "author_id": author_id,
                        "created_at": "2026-06-24T00:00:00Z",
                        "text": "hello",
                    }
                ],
            }
        ],
    }


def make_empty_thread_chat(chat_id: str = "chat-1", group_id: int = 23) -> dict:
    return {
        "id": chat_id,
        "access": {"group_ids": [group_id]},
        "users": [
            {"id": "customer-1", "type": "customer"},
            {"id": "agent-1", "type": "agent"},
        ],
        "threads": [
            {
                "id": "thread-1",
                "created_at": "2026-06-24T00:00:00Z",
                "events": [],
            }
        ],
    }


def test_ingress_contract_exports_shared_types():
    from app.channels.ingress import BaseIngressReceiver, IngressEvent, IngressNormalizeResult

    assert hasattr(BaseIngressReceiver, "receive_once")
    event = IngressEvent(source="polling_fallback", raw_action="polling.event", payload={"id": "raw-1"})
    result = IngressNormalizeResult(event=None, ignored=True, ignore_reason="agent_message")

    assert event.source == "polling_fallback"
    assert event.raw_action == "polling.event"
    assert result.ignored is True
    assert result.ignore_reason == "agent_message"


def test_polling_ingress_receiver_writes_inbound_events():
    from app.channels.livechat.polling_receiver import PollingIngressReceiver

    repository = FakeInboundRepository()
    client = FakeLiveChatClient(
        listed=[{"id": "chat-1", "access": {"group_ids": [23]}}],
        details={"chat-1": make_chat()},
    )

    result = asyncio.run(
        PollingIngressReceiver(
            client=client,
            repository=repository,
            allowed_group_ids={23},
            self_author_ids=set(),
        ).receive_once(limit=20)
    )

    assert result["inserted"] == 2
    assert result["duplicates"] == 0
    assert result["ignored"] == 0
    assert [event.standard_event_type for event in repository.events] == ["THREAD_STARTED", "MESSAGE_CREATED"]
    assert all(event.payload_json["ingress_source"] == "polling" for event in repository.events)


def test_polling_ingress_receiver_writes_intro_event_for_empty_thread_once():
    from app.channels.livechat.polling_receiver import PollingIngressReceiver

    repository = FakeInboundRepository()
    client = FakeLiveChatClient(
        listed=[{"id": "chat-1", "access": {"group_ids": [23]}}],
        details={"chat-1": make_empty_thread_chat()},
    )
    receiver = PollingIngressReceiver(
        client=client,
        repository=repository,
        allowed_group_ids={23},
        self_author_ids=set(),
    )

    first = asyncio.run(receiver.receive_once(limit=20))
    second = asyncio.run(receiver.receive_once(limit=20))

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicates"] == 1
    assert len(repository.events) == 1
    assert repository.events[0].standard_event_type == "THREAD_STARTED"
    assert repository.events[0].dedup_key == "livechat_polling:chat-1:thread-1:intro:chat-1:thread-1"


def test_polling_ingress_receiver_writes_intro_event_from_summary_without_get_chat():
    from app.channels.livechat.polling_receiver import PollingIngressReceiver

    class NoGetChatClient:
        async def list_chats(self, limit: int = 20) -> list[dict]:
            return [
                {
                    "id": "chat-1",
                    "access": {"group_ids": [23]},
                    "users": [{"id": "customer-1", "type": "customer"}],
                    "active_thread": {
                        "id": "thread-1",
                        "active": True,
                        "created_at": "2026-06-24T00:00:00Z",
                        "events": [],
                    },
                }
            ]

        async def get_chat(self, chat_id: str) -> dict:
            raise AssertionError("summary intro path should not call get_chat")

    repository = FakeInboundRepository()

    result = asyncio.run(
        PollingIngressReceiver(
            client=NoGetChatClient(),
            repository=repository,
            allowed_group_ids={23},
            self_author_ids=set(),
        ).receive_once(limit=20)
    )

    assert result["inserted"] == 1
    assert len(repository.events) == 1
    assert repository.events[0].standard_event_type == "THREAD_STARTED"


def test_polling_ingress_receiver_does_not_insert_duplicates():
    from app.channels.livechat.polling_receiver import PollingIngressReceiver

    repository = FakeInboundRepository()
    client = FakeLiveChatClient(
        listed=[{"id": "chat-1", "access": {"group_ids": [23]}}],
        details={"chat-1": make_chat()},
    )
    receiver = PollingIngressReceiver(
        client=client,
        repository=repository,
        allowed_group_ids={23},
        self_author_ids=set(),
    )

    first = asyncio.run(receiver.receive_once(limit=20))
    second = asyncio.run(receiver.receive_once(limit=20))

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["duplicates"] == 2
    assert len(repository.events) == 2


def test_polling_ingress_receiver_ignores_human_agent_and_generates_intro_for_self_greeting():
    from app.channels.livechat.polling_receiver import PollingIngressReceiver

    repository = FakeInboundRepository()
    client = FakeLiveChatClient(
        listed=[
            {"id": "chat-agent", "access": {"group_ids": [23]}},
            {"id": "chat-self", "access": {"group_ids": [23]}},
        ],
        details={
            "chat-agent": make_chat(chat_id="chat-agent", author_id="agent-1", author_type="agent", event_id="agent-event"),
            "chat-self": make_chat(chat_id="chat-self", author_id="self-agent", author_type="agent", event_id="self-event"),
        },
    )

    result = asyncio.run(
        PollingIngressReceiver(
            client=client,
            repository=repository,
            allowed_group_ids={23},
            self_author_ids={"self-agent"},
        ).receive_once(limit=20)
    )

    assert result["inserted"] == 1
    assert result["ignored"] == 2
    assert result["ignored_agent"] == 1
    assert result["ignored_self"] == 1
    assert len(repository.events) == 1
    assert repository.events[0].chat_id == "chat-self"
    assert repository.events[0].standard_event_type == "THREAD_STARTED"


def test_polling_ingress_receiver_ignores_unmatched_groups_without_get_chat():
    from app.channels.livechat.polling_receiver import PollingIngressReceiver

    repository = FakeInboundRepository()
    client = FakeLiveChatClient(
        listed=[{"id": "chat-15", "access": {"group_ids": [15]}}],
        details={"chat-15": make_chat(chat_id="chat-15", group_id=15)},
    )

    result = asyncio.run(
        PollingIngressReceiver(
            client=client,
            repository=repository,
            allowed_group_ids={23},
            self_author_ids=set(),
        ).receive_once(limit=20)
    )

    assert result["inserted"] == 0
    assert result["ignored_group"] == 1
    assert client.get_calls == []
    assert repository.events == []


def test_polling_receiver_worker_has_no_business_or_graph_logic():
    worker_source = Path("src/app/workers/polling_receiver.py").read_text()
    channel_source = Path("src/app/channels/livechat/polling_receiver.py").read_text()
    combined = f"{worker_source}\n{channel_source}"

    forbidden_terms = [
        "LangGraph",
        "GatewayConsumer",
        "RAG",
        "SOP",
        "build_graph",
        "process_event",
        "outbound_messages",
    ]

    for term in forbidden_terms:
        assert term not in combined
