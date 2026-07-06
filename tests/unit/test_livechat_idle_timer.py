import asyncio
from datetime import datetime, timedelta

from app.channels.livechat.sender_client import LiveChatApiError
from app.workers.livechat_idle_timer import CLOSE_TEXT, FOLLOWUP_TEXT, process_idle_conversation, process_idle_conversations


class FakeIdleRepository:
    def __init__(self, candidates=None, latest=None, customer_after=False) -> None:
        self.candidates = candidates or []
        self.latest = latest
        self.customer_after = customer_after
        self.inserted_messages = []
        self.slot_updates = []
        self.closed = []

    async def fetch_candidates(self, limit: int = 20) -> list[dict]:
        assert limit == 20
        return self.candidates

    async def fetch_latest_message(self, conversation_id: str) -> dict | None:
        return self.latest

    async def has_customer_message_after(self, conversation_id: str, created_at: datetime) -> bool:
        return self.customer_after

    async def insert_assistant_message(self, conversation: dict, text: str, now: datetime, source: str = "livechat_idle_timer") -> int:
        message_id = 100 + len(self.inserted_messages)
        self.inserted_messages.append(
            {
                "id": message_id,
                "conversation_id": conversation["conversation_id"],
                "text": text,
                "now": now,
                "source": source,
            }
        )
        self.latest = {
            "id": message_id,
            "sender_role": "assistant",
            "created_at": now,
            "text_content": text,
        }
        return message_id

    async def update_slot_memory(self, conversation_id: str, slot_memory: dict) -> None:
        self.slot_updates.append((conversation_id, dict(slot_memory)))

    async def mark_closed(self, conversation_id: str, slot_memory: dict) -> None:
        self.closed.append((conversation_id, dict(slot_memory)))


class FakeLiveChatClient:
    def __init__(self, close_error: Exception | None = None) -> None:
        self.sent_texts = []
        self.closed_chats = []
        self.close_error = close_error

    async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
        self.sent_texts.append((chat_id, thread_id, text))
        return {"event_id": f"event-{len(self.sent_texts)}"}

    async def deactivate_chat(self, chat_id: str) -> dict:
        self.closed_chats.append(chat_id)
        if self.close_error:
            raise self.close_error
        return {"success": True}


def make_conversation(slot_memory=None) -> dict:
    return {
        "conversation_id": "livechat:chat-1",
        "tenant_id": "default",
        "channel_type": "livechat",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "status": "AI_ACTIVE",
        "active_workflow": None,
        "slot_memory": slot_memory or {},
    }


def make_latest_assistant(created_at: datetime, message_id: int = 9) -> dict:
    return {
        "id": message_id,
        "sender_role": "assistant",
        "created_at": created_at,
        "text_content": "last bot reply",
    }


def test_idle_timer_waits_before_followup_threshold():
    now = datetime(2026, 7, 3, 9, 0, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=119)))
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "WAITING_FOR_FOLLOWUP_TIMER"
    assert client.sent_texts == []
    assert repository.slot_updates == []


def test_idle_timer_sends_followup_after_120_seconds():
    now = datetime(2026, 7, 3, 9, 0, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=120), message_id=9))
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "FOLLOWUP_SENT"
    assert client.sent_texts == [("chat-1", "thread-1", FOLLOWUP_TEXT)]
    assert repository.inserted_messages[0]["text"] == FOLLOWUP_TEXT
    slot_memory = repository.slot_updates[-1][1]
    assert slot_memory["idle_base_assistant_message_id"] == 9
    assert slot_memory["idle_followup_message_id"] == 100
    assert slot_memory["idle_followup_sent_at"] == "2026-07-03 09:00:00"


def test_idle_timer_persists_followup_send_failure_for_diagnostics():
    now = datetime(2026, 7, 3, 9, 0, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=120), message_id=9))

    class FailingLiveChatClient(FakeLiveChatClient):
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise RuntimeError("agent not in chat")

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(),
            repository=repository,
            sender_client=FailingLiveChatClient(),
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "FAILED_SEND_TEXT"
    assert result["error"] == "agent not in chat"
    slot_memory = repository.slot_updates[-1][1]
    assert slot_memory["idle_followup_last_error"] == "agent not in chat"
    assert slot_memory["idle_followup_failed_at"] == "2026-07-03 09:00:00"
    assert slot_memory["idle_last_failed_at"] == "2026-07-03 09:00:00"


def test_idle_timer_resets_when_customer_replies_after_followup():
    now = datetime(2026, 7, 3, 9, 3, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
    }
    repository = FakeIdleRepository(
        latest={"id": 101, "sender_role": "customer", "created_at": now - timedelta(seconds=10)},
        customer_after=True,
    )
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "RESET_BY_CUSTOMER"
    assert client.sent_texts == []
    assert repository.slot_updates[-1][1] == {}


def test_idle_timer_sends_close_text_and_closes_after_second_120_seconds():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "CLOSED"
    assert client.sent_texts == [("chat-1", "thread-1", CLOSE_TEXT)]
    assert client.closed_chats == ["chat-1"]
    assert repository.inserted_messages[0]["text"] == CLOSE_TEXT
    assert repository.closed[-1][1]["idle_close_sent_at"] == "2026-07-03 09:02:01"
    assert repository.closed[-1][1]["idle_closed_at"] == "2026-07-03 09:02:01"


def test_idle_timer_does_not_duplicate_close_text_when_close_retry_fails():
    now = datetime(2026, 7, 3, 9, 4, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_close_sent_at": "2026-07-03 09:02:01",
        "idle_close_message_id": 101,
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 2, 1), message_id=101))
    client = FakeLiveChatClient(close_error=RuntimeError("temporary close failure"))

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "FAILED_CLOSE_CHAT"
    assert client.sent_texts == []
    assert client.closed_chats == ["chat-1"]
    assert repository.slot_updates[-1][1]["idle_close_last_error"] == "temporary close failure"


def test_idle_timer_marks_closed_when_livechat_is_already_closed():
    now = datetime(2026, 7, 3, 9, 4, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_close_sent_at": "2026-07-03 09:02:01",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 2, 1), message_id=101))
    client = FakeLiveChatClient(close_error=LiveChatApiError(422, {"error": {"message": "Chat not active"}}))

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "CLOSED_ALREADY_IN_LIVECHAT"
    assert repository.closed[-1][1]["idle_closed_at"] == "2026-07-03 09:04:00"


def test_idle_timer_batch_uses_repository_candidates_so_human_active_can_be_filtered():
    repository = FakeIdleRepository(candidates=[])
    client = FakeLiveChatClient()

    results = asyncio.run(process_idle_conversations(object(), client, repository=repository))

    assert results == []
    assert client.sent_texts == []
