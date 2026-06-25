from app.db.repositories import InboundEventRepository, OutboundMessageRepository
from app.schemas.events import InboundEvent


class FakeCursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.sql = None
        self.args = None

    async def execute(self, sql, args):
        self.sql = sql
        self.args = args

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
