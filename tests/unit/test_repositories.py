from app.db.repositories import (
    ExternalCommandRepository,
    InboundEventRepository,
    OutboundMessageRepository,
    build_external_command_dedup_key,
)
from app.schemas.events import InboundEvent


class FakeCursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.sql = None
        self.args = None

    async def execute(self, sql, args):
        self.sql = sql
        self.args = args

    async def fetchall(self):
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def acquire(self):
        return FakeConnection(self._cursor)


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
        dedup_key="livechat_polling:chat-1:thread-1:event-1",
        payload_json={"ingress_source": "polling"},
        ignored=False,
    )


async def run_insert(rowcount: int):
    cursor = FakeCursor(rowcount=rowcount)
    repository = InboundEventRepository(FakePool(cursor))

    result = await repository.insert(make_inbound_event())

    return result, cursor


def make_outbound_message() -> dict:
    return {
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "action_type": "send_event",
        "message_type": "text",
        "payload_json": {"text": "hello"},
        "status": "PENDING",
        "inbound_event_id": 11,
        "conversation_id": "livechat:chat-1",
    }


async def run_outbound_insert_idempotent(rowcount: int):
    cursor = FakeCursor(rowcount=rowcount)
    cursor.lastrowid = 99
    repository = OutboundMessageRepository(FakePool(cursor))

    result = await repository.insert_idempotent(make_outbound_message())

    return result, cursor


def test_inbound_insert_uses_duplicate_key_update():
    import asyncio

    result, cursor = asyncio.run(run_insert(rowcount=1))

    assert "ON DUPLICATE KEY UPDATE id = id" in cursor.sql
    assert result == {"inserted": True, "duplicate": False}


def test_inbound_insert_reports_duplicate_without_failure():
    import asyncio

    result, _cursor = asyncio.run(run_insert(rowcount=0))

    assert result == {"inserted": False, "duplicate": True}


def test_outbound_insert_idempotent_uses_inbound_action_duplicate_key():
    import asyncio

    result, cursor = asyncio.run(run_outbound_insert_idempotent(rowcount=1))

    assert "ON DUPLICATE KEY UPDATE id = id" in cursor.sql
    assert "inbound_event_id" in cursor.sql
    assert "action_type" in cursor.sql
    assert result == {"inserted": True, "duplicate": False, "id": 99}


def test_outbound_insert_idempotent_reports_duplicate_without_failure():
    import asyncio

    result, _cursor = asyncio.run(run_outbound_insert_idempotent(rowcount=0))

    assert result == {"inserted": False, "duplicate": True, "id": None}


def make_external_command() -> dict:
    return {
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 11,
        "command_type": "telegram.send_case_card",
        "payload_json": {"intent": "deposit_missing"},
    }


async def run_external_insert_idempotent(rowcount: int):
    cursor = FakeCursor(rowcount=rowcount)
    cursor.lastrowid = 123
    repository = ExternalCommandRepository(FakePool(cursor))

    result = await repository.insert_idempotent(make_external_command())

    return result, cursor


def test_external_command_dedup_key_is_stable_for_payload_order():
    first = build_external_command_dedup_key(
        tenant_id="default",
        conversation_id="livechat:chat-1",
        inbound_event_id=11,
        command_type="backend.query",
        payload={"b": 2, "a": 1},
    )
    second = build_external_command_dedup_key(
        tenant_id="default",
        conversation_id="livechat:chat-1",
        inbound_event_id=11,
        command_type="backend.query",
        payload={"a": 1, "b": 2},
    )

    assert first == second
    assert first.startswith("default:livechat:chat-1:11:backend.query:")


def test_external_command_insert_idempotent_uses_dedup_key():
    import asyncio

    result, cursor = asyncio.run(run_external_insert_idempotent(rowcount=1))

    assert "INSERT INTO external_commands" in cursor.sql
    assert "ON DUPLICATE KEY UPDATE id = id" in cursor.sql
    assert cursor.args[5] == "telegram.send_case_card"
    assert cursor.args[10].startswith("default:livechat:chat-1:11:telegram.send_case_card:")
    assert result == {"inserted": True, "duplicate": False, "id": 123}


def test_external_command_insert_idempotent_reports_duplicate():
    import asyncio

    result, _cursor = asyncio.run(run_external_insert_idempotent(rowcount=0))

    assert result == {"inserted": False, "duplicate": True, "id": None}


def test_external_command_fetch_pending_filters_pending_by_created_at():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 1,
                    "payload_json": '{"ok": true}',
                    "status": "PENDING",
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = ExternalCommandRepository(FakePool(cursor))

    rows = asyncio.run(repository.fetch_pending(limit=5))

    assert "FROM external_commands" in cursor.sql
    assert "WHERE status = 'PENDING'" in cursor.sql
    assert "ORDER BY created_at ASC" in cursor.sql
    assert cursor.args == (5,)
    assert rows[0]["payload_json"] == {"ok": True}


def test_external_command_mark_status_methods():
    import asyncio

    async def run_marks():
        cursor = FakeCursor(rowcount=1)
        repository = ExternalCommandRepository(FakePool(cursor))

        await repository.mark_dry_run_done(1)
        dry_run_sql = cursor.sql
        await repository.mark_sent(2)
        sent_sql = cursor.sql
        await repository.mark_failed(3, "bad")
        failed_sql = cursor.sql
        failed_args = cursor.args
        await repository.mark_retryable(4, "temporary")
        retryable_sql = cursor.sql
        retryable_args = cursor.args
        return dry_run_sql, sent_sql, failed_sql, failed_args, retryable_sql, retryable_args

    dry_run_sql, sent_sql, failed_sql, failed_args, retryable_sql, retryable_args = asyncio.run(run_marks())

    assert "SET status = 'DRY_RUN_DONE'" in dry_run_sql
    assert "SET status = 'SENT'" in sent_sql
    assert "SET status = 'FAILED', last_error = %s" in failed_sql
    assert failed_args == ("bad", 3)
    assert "SET status = 'RETRYABLE', retry_count = retry_count + 1, last_error = %s" in retryable_sql
    assert retryable_args == ("temporary", 4)
