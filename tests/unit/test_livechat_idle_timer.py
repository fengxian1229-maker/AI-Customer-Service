import asyncio
import json
from datetime import datetime, timedelta

import pytest

from app.channels.livechat.sender_client import LiveChatApiError
from app.workers.livechat_idle_timer import (
    CLOSE_TEXT,
    FOLLOWUP_TEXT,
    LiveChatIdleTimerRepository,
    process_idle_conversation,
    process_idle_conversations,
)


class FakeIdleRepository:
    def __init__(
        self,
        candidates=None,
        latest=None,
        customer_after=False,
        unfinished_work=False,
        customer_activity_after_by_time=None,
        message_created_at_by_id=None,
    ) -> None:
        self.candidates = candidates or []
        self.latest = latest
        self.customer_after = customer_after
        self.unfinished_work = unfinished_work
        self.customer_activity_after_by_time = customer_activity_after_by_time or {}
        self.message_created_at_by_id = message_created_at_by_id or {}
        self.inserted_messages = []
        self.slot_updates = []
        self.closed = []
        self.handoff_requests = []
        self.message_created_at_lookups = []

    async def fetch_candidates(self, limit: int = 20) -> list[dict]:
        assert limit == 20
        return self.candidates

    async def fetch_latest_message(self, conversation_id: str) -> dict | None:
        return self.latest

    async def fetch_message_created_at(self, conversation_id: str, message_id: int) -> datetime | None:
        self.message_created_at_lookups.append((conversation_id, message_id))
        return self.message_created_at_by_id.get(message_id)

    async def has_unfinished_work(self, conversation_id: str) -> bool:
        return self.unfinished_work

    async def has_customer_activity_after(self, conversation: dict, created_at: datetime) -> bool:
        key = created_at.strftime("%Y-%m-%d %H:%M:%S")
        if key in self.customer_activity_after_by_time:
            return self.customer_activity_after_by_time[key]
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

    async def request_ai_failure_handoff(
        self,
        conversation: dict,
        slot_memory: dict,
        now: datetime,
        reason: str,
    ) -> bool:
        self.handoff_requests.append(
            {
                "conversation": conversation,
                "slot_memory": dict(slot_memory),
                "now": now,
                "reason": reason,
            }
        )
        return True


class FakeLiveChatClient:
    def __init__(self, close_error: Exception | None = None, send_error: Exception | None = None) -> None:
        self.sent_texts = []
        self.closed_chats = []
        self.close_error = close_error
        self.send_error = send_error

    async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
        self.sent_texts.append((chat_id, thread_id, text))
        if self.send_error:
            raise self.send_error
        return {"event_id": f"event-{len(self.sent_texts)}"}

    async def deactivate_chat(self, chat_id: str) -> dict:
        self.closed_chats.append(chat_id)
        if self.close_error:
            raise self.close_error
        return {"success": True}


class FakeFinalReplyService:
    def __init__(self, text: str, *, raise_error: bool = False) -> None:
        self.text = text
        self.raise_error = raise_error
        self.calls = []

    async def compose(self, state: dict) -> dict:
        self.calls.append(state)
        if self.raise_error:
            raise RuntimeError("final reply unavailable")
        return {
            **state,
            "final_response_text": self.text,
            "final_reply_result": {"status": "accepted", "confidence": 0.91},
        }


class FakeFallbackFinalReplyService(FakeFinalReplyService):
    def __init__(self, text: str, reason: str) -> None:
        super().__init__(text)
        self.reason = reason

    async def compose(self, state: dict) -> dict:
        self.calls.append(state)
        return {
            **state,
            "final_response_text": self.text,
            "final_reply_result": {"status": "fallback", "fallback_reason": self.reason},
        }


def make_conversation(slot_memory=None, status: str = "AI_ACTIVE") -> dict:
    return {
        "conversation_id": "livechat:chat-1",
        "tenant_id": "default",
        "channel_type": "livechat",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "status": status,
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


class HandoffBoundaryCursor:
    def __init__(
        self,
        status: str = "AI_ACTIVE",
        *,
        active_workflow: str | None = None,
        slot_memory: dict | None = None,
    ) -> None:
        self.status = status
        self.active_workflow = active_workflow
        self.slot_memory = slot_memory or {}
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, args):
        self.executions.append((sql, args))

    async def fetchone(self):
        return {
            "status": self.status,
            "active_workflow": self.active_workflow,
            "slot_memory": json.dumps(self.slot_memory),
        }


class HandoffBoundaryConnection:
    def __init__(
        self,
        status: str = "AI_ACTIVE",
        *,
        active_workflow: str | None = None,
        slot_memory: dict | None = None,
    ) -> None:
        self.cursor_instance = HandoffBoundaryCursor(
            status,
            active_workflow=active_workflow,
            slot_memory=slot_memory,
        )
        self.began = False
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args):
        return self.cursor_instance

    async def begin(self):
        self.began = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class HandoffBoundaryPool:
    def __init__(
        self,
        status: str = "AI_ACTIVE",
        *,
        active_workflow: str | None = None,
        slot_memory: dict | None = None,
    ) -> None:
        self.connection = HandoffBoundaryConnection(
            status,
            active_workflow=active_workflow,
            slot_memory=slot_memory,
        )

    def acquire(self):
        return self.connection


class IdleRepositoryBoundaryCursor:
    def __init__(self, row=None) -> None:
        self.sql = None
        self.args = None
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, args):
        self.sql = sql
        self.args = args

    async def fetchone(self):
        return self.row


class IdleRepositoryBoundaryConnection:
    def __init__(self, row=None) -> None:
        self.cursor_instance = IdleRepositoryBoundaryCursor(row)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args):
        return self.cursor_instance


class IdleRepositoryBoundaryPool:
    def __init__(self, row=None) -> None:
        self.connection = IdleRepositoryBoundaryConnection(row)

    def acquire(self):
        return self.connection


def test_idle_repository_customer_activity_is_chat_scoped():
    cutoff = datetime(2026, 7, 3, 9, 0, 0)
    pool = IdleRepositoryBoundaryPool()
    repository = LiveChatIdleTimerRepository(pool)

    asyncio.run(repository.has_customer_activity_after(make_conversation(), cutoff))

    cursor = pool.connection.cursor_instance
    assert cursor.args == ("chat-1", cutoff)
    assert "effective_activity_at > %s" in cursor.sql
    assert "COALESCE(occurred_at, created_at) > %s" not in cursor.sql
    assert "processed = 0" not in cursor.sql
    assert "thread_id =" not in cursor.sql


def test_idle_repository_fetches_message_created_at_by_conversation_and_id():
    created_at = datetime(2026, 7, 3, 8, 58, 0)
    pool = IdleRepositoryBoundaryPool({"created_at": created_at})
    repository = LiveChatIdleTimerRepository(pool)

    result = asyncio.run(repository.fetch_message_created_at("livechat:chat-1", 9))

    cursor = pool.connection.cursor_instance
    assert result == created_at
    assert cursor.args == ("livechat:chat-1", 9)
    assert "FROM conversation_messages" in cursor.sql


def test_idle_handoff_command_uses_stable_operation_dedup_key_across_failure_reasons(monkeypatch):
    captured_commands = []

    async def capture_insert(self, conn, command):
        captured_commands.append(dict(command))
        return {"inserted": True, "duplicate": False, "id": len(captured_commands)}

    monkeypatch.setattr(
        "app.workers.livechat_idle_timer.ExternalCommandRepository.insert_idempotent_on_connection",
        capture_insert,
    )

    for reason in ("timeout", "exception"):
        repository = LiveChatIdleTimerRepository(HandoffBoundaryPool())
        asyncio.run(
            repository.request_ai_failure_handoff(
                make_conversation(),
                {"ai_service_failure_reason": reason},
                datetime(2026, 7, 3, 9, 2, 1),
                reason,
            )
        )

    assert captured_commands[0]["dedup_key"] == captured_commands[1]["dedup_key"]
    assert [command["payload_json"]["failure_reason"] for command in captured_commands] == ["timeout", "exception"]
    assert all(command["payload_json"]["handoff_ack_mode"] == "direct_notice" for command in captured_commands)


@pytest.mark.parametrize(
    ("status", "expected_requested", "expected_commands"),
    [
        ("HUMAN_ACTIVE", False, 0),
        ("CLOSED", False, 0),
        ("HANDOFF_REQUESTED", False, 0),
    ],
)
def test_idle_handoff_repository_guards_locked_terminal_states(
    monkeypatch,
    status,
    expected_requested,
    expected_commands,
):
    captured_commands = []

    async def capture_insert(self, conn, command):
        captured_commands.append(dict(command))
        return {"inserted": True, "duplicate": False, "id": 1}

    monkeypatch.setattr(
        "app.workers.livechat_idle_timer.ExternalCommandRepository.insert_idempotent_on_connection",
        capture_insert,
    )
    pool = HandoffBoundaryPool(status)
    repository = LiveChatIdleTimerRepository(pool)

    requested = asyncio.run(
        repository.request_ai_failure_handoff(
            make_conversation(),
            {"ai_service_failure_reason": "timeout"},
            datetime(2026, 7, 3, 9, 2, 1),
            "timeout",
        )
    )

    assert requested is expected_requested
    assert len(captured_commands) == expected_commands
    assert pool.connection.began is True
    assert pool.connection.committed is True
    assert pool.connection.rolled_back is False
    assert "FOR UPDATE" in pool.connection.cursor_instance.executions[0][0]
    assert all("UPDATE conversation_states" not in sql for sql, _args in pool.connection.cursor_instance.executions)


def test_idle_handoff_repository_loses_race_to_normal_handoff_workflow(monkeypatch):
    captured_commands = []

    async def capture_insert(self, conn, command):
        captured_commands.append(dict(command))
        return {"inserted": True, "duplicate": False, "id": 1}

    monkeypatch.setattr(
        "app.workers.livechat_idle_timer.ExternalCommandRepository.insert_idempotent_on_connection",
        capture_insert,
    )
    pool = HandoffBoundaryPool(
        "AI_ACTIVE",
        active_workflow="human_handoff",
        slot_memory={"handoff_reason": "customer_requested"},
    )
    repository = LiveChatIdleTimerRepository(pool)

    requested = asyncio.run(
        repository.request_ai_failure_handoff(
            make_conversation(),
            {"handoff_reason": "stale", "ai_service_failure_reason": "timeout"},
            datetime(2026, 7, 3, 9, 2, 1),
            "timeout",
        )
    )

    assert requested is False
    assert captured_commands == []
    assert all("UPDATE conversation_states" not in sql for sql, _args in pool.connection.cursor_instance.executions)


def test_idle_handoff_repository_merges_failure_fields_into_locked_current_slot_memory(monkeypatch):
    async def capture_insert(self, conn, command):
        return {"inserted": True, "duplicate": False, "id": 1}

    monkeypatch.setattr(
        "app.workers.livechat_idle_timer.ExternalCommandRepository.insert_idempotent_on_connection",
        capture_insert,
    )
    pool = HandoffBoundaryPool(
        "WAITING_EXTERNAL",
        active_workflow="deposit_missing",
        slot_memory={"account_or_phone": "locked-current", "case_id": "case-7"},
    )
    repository = LiveChatIdleTimerRepository(pool)

    requested = asyncio.run(
        repository.request_ai_failure_handoff(
            make_conversation(),
            {
                "account_or_phone": "stale-candidate",
                "ai_service_failure_handoff_at": "2026-07-03 09:02:01",
                "ai_service_failure_reason": "timeout",
                "ai_service_failure_source": "livechat_idle_timer",
            },
            datetime(2026, 7, 3, 9, 2, 1),
            "timeout",
        )
    )

    update_args = pool.connection.cursor_instance.executions[1][1]
    persisted_slot_memory = json.loads(update_args[0])
    assert requested is True
    assert persisted_slot_memory == {
        "account_or_phone": "locked-current",
        "case_id": "case-7",
        "ai_service_failure_handoff_at": "2026-07-03 09:02:01",
        "ai_service_failure_reason": "timeout",
        "ai_service_failure_source": "livechat_idle_timer",
    }
    assert "status, active_workflow, slot_memory" in pool.connection.cursor_instance.executions[0][0]


def test_idle_handoff_repository_commits_state_and_command_on_one_connection(monkeypatch):
    inserted_on = []

    async def capture_insert(self, conn, command):
        inserted_on.append(conn)
        return {"inserted": True, "duplicate": False, "id": 1}

    monkeypatch.setattr(
        "app.workers.livechat_idle_timer.ExternalCommandRepository.insert_idempotent_on_connection",
        capture_insert,
    )
    pool = HandoffBoundaryPool("AI_ACTIVE")
    repository = LiveChatIdleTimerRepository(pool)

    requested = asyncio.run(
        repository.request_ai_failure_handoff(
            make_conversation(),
            {"ai_service_failure_reason": "timeout"},
            datetime(2026, 7, 3, 9, 2, 1),
            "timeout",
        )
    )

    assert requested is True
    assert inserted_on == [pool.connection]
    assert pool.connection.began is True
    assert pool.connection.committed is True
    assert pool.connection.rolled_back is False
    assert "FOR UPDATE" in pool.connection.cursor_instance.executions[0][0]
    assert "UPDATE conversation_states" in pool.connection.cursor_instance.executions[1][0]


def test_idle_handoff_repository_rolls_back_state_when_command_insert_fails(monkeypatch):
    inserted_on = []

    async def fail_insert(self, conn, command):
        inserted_on.append(conn)
        raise RuntimeError("command insert unavailable")

    monkeypatch.setattr(
        "app.workers.livechat_idle_timer.ExternalCommandRepository.insert_idempotent_on_connection",
        fail_insert,
    )
    pool = HandoffBoundaryPool("AI_ACTIVE")
    repository = LiveChatIdleTimerRepository(pool)

    with pytest.raises(RuntimeError, match="command insert unavailable"):
        asyncio.run(
            repository.request_ai_failure_handoff(
                make_conversation(),
                {"ai_service_failure_reason": "timeout"},
                datetime(2026, 7, 3, 9, 2, 1),
                "timeout",
            )
        )

    assert inserted_on == [pool.connection]
    assert pool.connection.began is True
    assert pool.connection.committed is False
    assert pool.connection.rolled_back is True


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


def test_idle_timer_closes_stale_backlog_without_sending_delayed_followup():
    now = datetime(2026, 7, 3, 9, 30, 1)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(minutes=30, seconds=1)))
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

    assert result["status"] == "CLOSED_STALE_BACKLOG"
    assert client.sent_texts == []
    assert client.closed_chats == []
    assert repository.closed[-1][1]["idle_closed_at"] == "2026-07-03 09:30:01"
    assert repository.closed[-1][1]["idle_close_reason"] == "stale_idle_backlog"


def test_idle_timer_does_not_close_stale_backlog_with_newer_customer_activity():
    now = datetime(2026, 7, 3, 9, 30, 1)
    repository = FakeIdleRepository(
        latest=make_latest_assistant(now - timedelta(minutes=30, seconds=1)),
        customer_after=True,
    )
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

    assert result["status"] == "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY"
    assert repository.closed == []
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_does_not_close_stale_backlog_when_customer_activity_check_fails():
    class FailingActivityRepository(FakeIdleRepository):
        async def has_customer_activity_after(self, conversation: dict, created_at: datetime) -> bool:
            raise RuntimeError("activity query unavailable")

    now = datetime(2026, 7, 3, 9, 30, 1)
    repository = FailingActivityRepository(latest=make_latest_assistant(now - timedelta(minutes=30, seconds=1)))
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

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED"
    assert result["error"] == "activity query unavailable"
    assert repository.closed == []
    assert client.sent_texts == []
    assert client.closed_chats == []


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


def test_idle_timer_skips_followup_when_customer_activity_arrived_after_latest_assistant():
    now = datetime(2026, 7, 3, 9, 0, 0)
    repository = FakeIdleRepository(
        latest=make_latest_assistant(now - timedelta(seconds=120), message_id=9),
        customer_after=True,
    )
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

    assert result["status"] == "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY"
    assert client.sent_texts == []
    assert repository.inserted_messages == []
    assert repository.slot_updates == []


def test_idle_timer_does_not_send_followup_when_customer_activity_check_fails():
    now = datetime(2026, 7, 3, 9, 0, 0)

    class FailingCustomerActivityRepository(FakeIdleRepository):
        async def has_customer_activity_after(self, conversation: dict, created_at: datetime) -> bool:
            raise RuntimeError("customer activity check unavailable")

    repository = FailingCustomerActivityRepository(
        latest=make_latest_assistant(now - timedelta(seconds=120), message_id=9)
    )
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

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED"
    assert result["error"] == "customer activity check unavailable"
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_followup_uses_final_reply_language_text():
    now = datetime(2026, 7, 3, 9, 0, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=120), message_id=9))
    client = FakeLiveChatClient()
    final_reply_service = FakeFinalReplyService("Are you still there? I can keep helping here.")

    result = asyncio.run(
        process_idle_conversation(
            make_conversation({"last_reply_language": "en"}),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "FOLLOWUP_SENT"
    assert client.sent_texts == [("chat-1", "thread-1", "Are you still there? I can keep helping here.")]
    assert repository.inserted_messages[0]["text"] == "Are you still there? I can keep helping here."
    assert final_reply_service.calls[0]["reply_language"] == "en"
    assert final_reply_service.calls[0]["response_text_fallback"] == FOLLOWUP_TEXT
    assert final_reply_service.calls[0]["node_reply_template"] == "idle_followup"


def test_idle_timer_does_not_send_followup_when_customer_arrives_during_finalization():
    now = datetime(2026, 7, 3, 9, 0, 0)
    latest_created_at = now - timedelta(seconds=120)
    repository = FakeIdleRepository(latest=make_latest_assistant(latest_created_at, message_id=9))
    client = FakeLiveChatClient()

    class CustomerArrivalFinalReplyService(FakeFinalReplyService):
        async def compose(self, state: dict) -> dict:
            repository.customer_after = True
            return await super().compose(state)

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=CustomerArrivalFinalReplyService(FOLLOWUP_TEXT),
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY"
    assert client.sent_texts == []
    assert repository.inserted_messages == []


def test_idle_timer_close_uses_final_reply_language_text():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "last_reply_language": "es",
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
    client = FakeLiveChatClient()
    final_reply_service = FakeFinalReplyService("Cerraré este chat por ahora. Puedes escribirnos de nuevo cuando necesites ayuda.")

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "CLOSED"
    assert client.sent_texts == [
        ("chat-1", "thread-1", "Cerraré este chat por ahora. Puedes escribirnos de nuevo cuando necesites ayuda.")
    ]
    assert repository.inserted_messages[0]["text"] == "Cerraré este chat por ahora. Puedes escribirnos de nuevo cuando necesites ayuda."
    assert final_reply_service.calls[0]["reply_language"] == "es"
    assert final_reply_service.calls[0]["response_text_fallback"] == CLOSE_TEXT
    assert final_reply_service.calls[0]["node_reply_template"] == "idle_close"


@pytest.mark.parametrize(
    ("language", "expected_notice"),
    [
        ("zh-Hans", "很抱歉，AI客服当前出现临时故障。为了不影响您继续处理问题，现为您转接人工客服继续协助。"),
        ("zh-Hant", "很抱歉，AI客服目前出現暫時故障。為了不影響您繼續處理問題，現為您轉接真人客服繼續協助。"),
        ("en", "Sorry, the automated assistant is having a temporary technical issue. To avoid delaying your case, I will transfer you to a human agent for continued help."),
        ("es", "Lo sentimos, el asistente automático tuvo un inconveniente técnico temporal. Para no afectar la atención de su caso, le transferiremos con un agente humano para que continúe ayudándole."),
        ("tl", "Paumanhin, pansamantalang nagkaroon ng teknikal na problema ang automated assistant. Upang hindi maantala ang iyong concern, ililipat ka namin sa isang human agent para patuloy kang matulungan."),
        ("th", "ขออภัย ผู้ช่วยอัตโนมัติเกิดปัญหาทางเทคนิคชั่วคราว เพื่อไม่ให้การดำเนินการของคุณล่าช้า เราจะโอนคุณไปยังเจ้าหน้าที่เพื่อช่วยเหลือต่อ"),
        ("my", "တောင်းပန်ပါတယ်၊ အလိုအလျောက်အကူစနစ်တွင် ယာယီနည်းပညာပြဿနာ ဖြစ်ပေါ်နေပါသည်။ သင့်ကိစ္စ မနှောင့်နှေးစေရန် လူသားဝန်ထမ်းထံ လွှဲပြောင်းပေးပါမည်။"),
        ("ms", "Maaf, pembantu automatik sedang mengalami masalah teknikal sementara. Untuk mengelakkan urusan anda tertangguh, kami akan memindahkan anda kepada ejen manusia untuk bantuan lanjut."),
        ("unknown", "Lo sentimos, el asistente automático tuvo un inconveniente técnico temporal. Para no afectar la atención de su caso, le transferiremos con un agente humano para que continúe ayudándole."),
    ],
)
def test_idle_timer_requests_localized_human_handoff_without_second_llm_call(language, expected_notice):
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "last_reply_language": language,
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
    client = FakeLiveChatClient()
    final_reply_service = FakeFallbackFinalReplyService(CLOSE_TEXT, "timeout")

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "AI_FAILURE_HANDOFF_REQUESTED"
    assert client.sent_texts == [("chat-1", "thread-1", expected_notice)]
    assert client.closed_chats == []
    assert repository.handoff_requests[0]["reason"] == "timeout"
    assert len(final_reply_service.calls) == 1


@pytest.mark.parametrize("failure_reason", ["exception", "provider_failure"])
def test_idle_timer_requests_human_handoff_for_terminal_final_reply_failures(failure_reason):
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "last_reply_language": "en",
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
    client = FakeLiveChatClient()
    final_reply_service = FakeFallbackFinalReplyService(CLOSE_TEXT, failure_reason)

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "AI_FAILURE_HANDOFF_REQUESTED"
    assert "human agent" in client.sent_texts[0][2]
    assert client.closed_chats == []
    assert repository.handoff_requests[0]["reason"] == failure_reason
    assert len(final_reply_service.calls) == 1


def test_idle_timer_stays_in_handoff_when_handoff_notice_send_fails():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "last_reply_language": "zh",
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))

    class FailingHandoffNoticeClient(FakeLiveChatClient):
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise RuntimeError("handoff notice unavailable")

    client = FailingHandoffNoticeClient()
    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=FakeFallbackFinalReplyService(CLOSE_TEXT, "timeout"),
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "FAILED_HANDOFF_NOTICE"
    assert result["error"] == "handoff notice unavailable"
    assert repository.handoff_requests[0]["reason"] == "timeout"
    assert client.closed_chats == []


def test_idle_timer_stale_cycle_does_not_notice_or_close_after_terminal_state_wins():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "last_reply_language": "zh",
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }

    class TerminalStateRepository(FakeIdleRepository):
        async def request_ai_failure_handoff(
            self,
            conversation: dict,
            slot_memory: dict,
            now: datetime,
            reason: str,
        ) -> bool:
            return False

    repository = TerminalStateRepository(
        latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100)
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
            final_reply_service=FakeFallbackFinalReplyService(CLOSE_TEXT, "timeout"),
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "SKIPPED_TERMINAL_STATE"
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_does_not_send_close_when_customer_arrives_during_finalization():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
    client = FakeLiveChatClient()

    class CustomerArrivalFinalReplyService(FakeFinalReplyService):
        async def compose(self, state: dict) -> dict:
            repository.customer_after = True
            return await super().compose(state)

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=CustomerArrivalFinalReplyService(CLOSE_TEXT),
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY"
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_close_uses_base_cutoff_when_earlier_activity_appears_during_finalization():
    now = datetime(2026, 7, 3, 9, 4, 1)
    base_created_at = datetime(2026, 7, 3, 9, 0, 0)
    customer_activity_at = datetime(2026, 7, 3, 9, 1, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:02:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 09:00:00",
    }

    class DelayedVisibilityRepository(FakeIdleRepository):
        def __init__(self) -> None:
            super().__init__(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 2, 0), message_id=100))
            self.customer_activity_visible = False
            self.activity_cutoffs = []

        async def has_customer_activity_after(self, conversation: dict, cutoff: datetime) -> bool:
            self.activity_cutoffs.append(cutoff)
            return self.customer_activity_visible and customer_activity_at > cutoff

    repository = DelayedVisibilityRepository()
    client = FakeLiveChatClient()

    class CustomerActivityBecomesVisibleFinalReplyService(FakeFinalReplyService):
        async def compose(self, state: dict) -> dict:
            repository.customer_activity_visible = True
            return await super().compose(state)

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=CustomerActivityBecomesVisibleFinalReplyService(CLOSE_TEXT),
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY"
    assert client.sent_texts == []
    assert client.closed_chats == []
    assert repository.activity_cutoffs[-1] == base_created_at


def test_idle_timer_does_not_send_close_when_customer_activity_check_fails_after_finalization():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
    client = FakeLiveChatClient()

    class FailingCustomerActivityCheckFinalReplyService(FakeFinalReplyService):
        async def compose(self, state: dict) -> dict:
            async def raise_customer_activity_check(conversation: dict, created_at: datetime) -> bool:
                raise RuntimeError("customer activity check unavailable")

            repository.has_customer_activity_after = raise_customer_activity_check
            return await super().compose(state)

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=FailingCustomerActivityCheckFinalReplyService(CLOSE_TEXT),
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED"
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_non_terminal_final_reply_fallback_uses_fallback_text():
    now = datetime(2026, 7, 3, 9, 0, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=120), message_id=9))
    client = FakeLiveChatClient()
    final_reply_service = FakeFallbackFinalReplyService(FOLLOWUP_TEXT, "empty_model_text")

    result = asyncio.run(
        process_idle_conversation(
            make_conversation({"last_reply_language": "en"}),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "FOLLOWUP_SENT"
    assert client.sent_texts == [("chat-1", "thread-1", FOLLOWUP_TEXT)]
    assert repository.inserted_messages[0]["text"] == FOLLOWUP_TEXT


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
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
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


def test_idle_timer_restarts_followup_after_customer_reply_and_new_assistant_message():
    now = datetime(2026, 7, 3, 9, 5, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(
        latest=make_latest_assistant(now - timedelta(seconds=121), message_id=102),
        customer_activity_after_by_time={
            "2026-07-03 09:00:00": True,
            "2026-07-03 09:02:59": False,
        },
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

    assert result["status"] == "FOLLOWUP_SENT"
    assert client.sent_texts == [("chat-1", "thread-1", FOLLOWUP_TEXT)]
    assert repository.slot_updates[0][1] == {}
    refreshed_slot_memory = repository.slot_updates[-1][1]
    assert refreshed_slot_memory["idle_base_assistant_message_id"] == 102
    assert refreshed_slot_memory["idle_followup_message_id"] == 100
    assert refreshed_slot_memory["idle_followup_sent_at"] == "2026-07-03 09:05:00"


def test_idle_timer_waiting_external_restarts_followup_when_no_pending_work():
    now = datetime(2026, 7, 3, 9, 5, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(
        latest=make_latest_assistant(now - timedelta(seconds=120), message_id=102),
        unfinished_work=False,
        customer_activity_after_by_time={
            "2026-07-03 09:00:00": True,
            "2026-07-03 09:03:00": False,
        },
    )
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(slot_memory, status="WAITING_EXTERNAL"),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "FOLLOWUP_SENT"
    assert client.sent_texts == [("chat-1", "thread-1", FOLLOWUP_TEXT)]
    assert repository.slot_updates[0][1] == {}
    assert repository.slot_updates[-1][1]["idle_base_assistant_message_id"] == 102


def test_idle_timer_waiting_external_skips_when_pending_work_exists():
    now = datetime(2026, 7, 3, 9, 5, 0)
    repository = FakeIdleRepository(
        latest=make_latest_assistant(now - timedelta(seconds=120), message_id=102),
        unfinished_work=True,
    )
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation(status="WAITING_EXTERNAL"),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "SKIPPED_PENDING_WORK"
    assert client.sent_texts == []
    assert repository.slot_updates == []


def test_idle_timer_skips_when_ai_service_failure_handoff_was_requested():
    now = datetime(2026, 7, 3, 9, 5, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=300), message_id=102))
    client = FakeLiveChatClient()

    result = asyncio.run(
        process_idle_conversation(
            make_conversation({"ai_service_failure_handoff_at": "2026-07-03 09:00:00"}),
            repository=repository,
            sender_client=client,
            followup_seconds=120,
            close_seconds=120,
            now=now,
        )
    )

    assert result["status"] == "SKIPPED_AI_FAILURE_HANDOFF"
    assert client.sent_texts == []
    assert client.closed_chats == []
    assert repository.slot_updates == []


def test_idle_timer_sends_close_text_and_closes_after_second_120_seconds():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
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


def test_idle_timer_skips_legacy_close_state_without_idle_cycle_cutoff():
    now = datetime(2026, 7, 3, 9, 4, 0)
    slot_memory = {
        "idle_close_sent_at": "2026-07-03 09:02:01",
        "idle_close_message_id": 101,
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 2, 1), message_id=101))
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

    assert result["status"] == "SKIPPED_MISSING_IDLE_CYCLE_CUTOFF"
    assert client.closed_chats == []
    assert repository.closed == []


def test_idle_timer_recovers_and_persists_legacy_base_cutoff_from_message_id():
    now = datetime(2026, 7, 3, 9, 2, 1)
    base_created_at = datetime(2026, 7, 3, 8, 58, 0, 123456)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
    }
    repository = FakeIdleRepository(
        latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100),
        message_created_at_by_id={9: base_created_at},
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

    assert result["status"] == "CLOSED"
    assert repository.message_created_at_lookups == [("livechat:chat-1", 9)]
    assert repository.slot_updates[0][1]["idle_base_assistant_created_at"] == "2026-07-03 08:58:00.123456"
    assert client.sent_texts == [("chat-1", "thread-1", CLOSE_TEXT)]
    assert client.closed_chats == ["chat-1"]


def test_idle_timer_unrecoverable_legacy_base_cutoff_does_not_send_or_close():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
    }
    repository = FakeIdleRepository(
        latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100)
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

    assert result["status"] == "SKIPPED_MISSING_IDLE_CYCLE_CUTOFF"
    assert repository.message_created_at_lookups == [("livechat:chat-1", 9)]
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_does_not_close_for_customer_activity_in_another_thread():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
    }

    class CrossThreadCustomerActivityRepository(FakeIdleRepository):
        def __init__(self) -> None:
            super().__init__(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
            self.activity = {
                "chat_id": "chat-1",
                "thread_id": "thread-2",
                "occurred_at": None,
                "created_at": datetime(2026, 7, 3, 9, 1, 0),
            }
            self.activity_calls = []

        async def has_customer_activity_after(self, conversation: dict, cutoff: datetime) -> bool:
            self.activity_calls.append((dict(conversation), cutoff))
            activity_time = self.activity["occurred_at"] or self.activity["created_at"]
            return self.activity["chat_id"] == conversation["chat_id"] and activity_time > cutoff

    repository = CrossThreadCustomerActivityRepository()
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

    assert result["status"] == "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY"
    assert repository.activity_calls[0][0]["thread_id"] == "thread-1"
    assert repository.activity["thread_id"] == "thread-2"
    assert client.sent_texts == []
    assert client.closed_chats == []


def test_idle_timer_does_not_deactivate_when_customer_arrives_after_close_text():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))

    class CustomerArrivalLiveChatClient(FakeLiveChatClient):
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            result = await super().send_text(chat_id, thread_id, text)
            repository.customer_after = True
            return result

    client = CustomerArrivalLiveChatClient()

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

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY"
    assert len(client.sent_texts) == 1
    assert client.closed_chats == []


def test_idle_timer_does_not_deactivate_when_earlier_customer_activity_becomes_visible_after_close_text():
    now = datetime(2026, 7, 3, 9, 2, 1)
    customer_activity_at = datetime(2026, 7, 3, 9, 1, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }

    class DelayedCustomerActivityRepository(FakeIdleRepository):
        def __init__(self) -> None:
            super().__init__(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))
            self.customer_activity_visible = False

        async def has_customer_activity_after(self, conversation: dict, created_at: datetime) -> bool:
            return self.customer_activity_visible and created_at < customer_activity_at

    repository = DelayedCustomerActivityRepository()

    class DelayedCustomerActivityLiveChatClient(FakeLiveChatClient):
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            result = await super().send_text(chat_id, thread_id, text)
            repository.customer_activity_visible = True
            return result

    client = DelayedCustomerActivityLiveChatClient()

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

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY"
    assert len(client.sent_texts) == 1
    assert client.closed_chats == []


def test_idle_timer_does_not_deactivate_when_final_customer_activity_check_fails():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100))

    class FailingCustomerActivityCheckLiveChatClient(FakeLiveChatClient):
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            result = await super().send_text(chat_id, thread_id, text)

            async def raise_customer_activity_check(conversation: dict, created_at: datetime) -> bool:
                raise RuntimeError("customer activity check unavailable")

            repository.has_customer_activity_after = raise_customer_activity_check
            return result

    client = FailingCustomerActivityCheckLiveChatClient()

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

    assert result["status"] == "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED"
    assert len(client.sent_texts) == 1
    assert client.closed_chats == []


def test_idle_timer_skips_close_when_customer_activity_arrived_after_followup():
    now = datetime(2026, 7, 3, 9, 2, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
    }
    repository = FakeIdleRepository(
        latest=make_latest_assistant(datetime(2026, 7, 3, 9, 0, 0), message_id=100),
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

    assert result["status"] == "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY"
    assert client.sent_texts == []
    assert client.closed_chats == []
    assert repository.inserted_messages == []


def test_idle_timer_skips_close_when_customer_activity_arrived_after_base_assistant_before_followup():
    now = datetime(2026, 7, 3, 9, 4, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:02:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 9,
        "idle_base_assistant_created_at": "2026-07-03 09:00:00",
    }
    repository = FakeIdleRepository(
        latest=make_latest_assistant(datetime(2026, 7, 3, 9, 2, 0), message_id=100),
        customer_activity_after_by_time={
            "2026-07-03 09:02:00": False,
            "2026-07-03 09:00:00": True,
        },
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

    assert result["status"] == "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY"
    assert client.sent_texts == []
    assert client.closed_chats == []
    assert repository.inserted_messages == []


def test_idle_timer_sends_close_after_refreshed_followup_when_customer_stays_silent():
    now = datetime(2026, 7, 3, 9, 7, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:05:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 102,
        "idle_base_assistant_created_at": "2026-07-03 09:03:00",
    }
    repository = FakeIdleRepository(latest=make_latest_assistant(datetime(2026, 7, 3, 9, 5, 0), message_id=100))
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
    assert repository.closed[-1][1]["idle_close_sent_at"] == "2026-07-03 09:07:01"
    assert repository.closed[-1][1]["idle_closed_at"] == "2026-07-03 09:07:01"


def test_idle_timer_customer_reply_after_refreshed_followup_prevents_close():
    now = datetime(2026, 7, 3, 9, 7, 1)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:05:00",
        "idle_followup_message_id": 100,
        "idle_base_assistant_message_id": 102,
    }
    repository = FakeIdleRepository(
        latest={"id": 103, "sender_role": "customer", "created_at": now - timedelta(seconds=10)},
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
    assert client.closed_chats == []
    assert repository.slot_updates[-1][1] == {}


def test_idle_timer_does_not_duplicate_close_text_when_close_retry_fails():
    now = datetime(2026, 7, 3, 9, 4, 0)
    slot_memory = {
        "idle_followup_sent_at": "2026-07-03 09:00:00",
        "idle_close_sent_at": "2026-07-03 09:02:01",
        "idle_close_message_id": 101,
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
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
        "idle_base_assistant_created_at": "2026-07-03 08:58:00",
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


def test_idle_timer_marks_closed_when_followup_send_reports_chat_inactive():
    now = datetime(2026, 7, 3, 9, 2, 0)
    repository = FakeIdleRepository(latest=make_latest_assistant(now - timedelta(seconds=120)))
    client = FakeLiveChatClient(
        send_error=LiveChatApiError(
            400,
            {
                "error": {"type": "chat_inactive", "message": "No active chat thread"},
                "path": "/agent/action/add_user_to_chat",
            },
        )
    )

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

    assert result["status"] == "CLOSED_ALREADY_IN_LIVECHAT"
    assert repository.closed[-1][1]["idle_closed_at"] == "2026-07-03 09:02:00"
    assert "idle_last_failed_at" not in repository.closed[-1][1]


def test_idle_timer_batch_uses_repository_candidates_so_human_active_can_be_filtered():
    repository = FakeIdleRepository(candidates=[])
    client = FakeLiveChatClient()

    results = asyncio.run(process_idle_conversations(object(), client, repository=repository))

    assert results == []
    assert client.sent_texts == []
