from app.db.repositories import (
    ExternalCommandRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    InboundEventRepository,
    OutboundMessageRepository,
    build_external_command_dedup_key,
    build_external_command_result_dedup_key,
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
        self.began = False
        self.committed = False
        self.rolled_back = False

    def cursor(self, *args, **kwargs):
        return self._cursor

    async def begin(self):
        self.began = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.last_connection = None

    def acquire(self):
        self.last_connection = FakeConnection(self._cursor)
        return self.last_connection


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
    assert "SET status = 'FAILED'" in failed_sql
    assert "last_error = %s" in failed_sql
    assert "locked_by = NULL" in failed_sql
    assert failed_args == ("bad", 3)
    assert "SET status = 'RETRYABLE'" in retryable_sql
    assert "retry_count = retry_count + 1" in retryable_sql
    assert "last_error = %s" in retryable_sql
    assert retryable_args == ("temporary", 4)


def test_external_command_lease_pending_uses_skip_locked_and_returns_rows():
    import asyncio

    class LeaseCursor(FakeCursor):
        def __init__(self) -> None:
            super().__init__(rowcount=1)
            self.executed = []

        async def execute(self, sql, args):
            self.sql = sql
            self.args = args
            self.executed.append((sql, args))

        async def fetchall(self):
            if self.sql.strip().startswith("SELECT id\n        FROM external_commands"):
                return [{"id": 1}]
            return [
                {
                    "id": 1,
                    "payload_json": '{"ok": true}',
                    "status": "PENDING",
                    "locked_by": "worker-a",
                }
            ]

    cursor = LeaseCursor()
    repository = ExternalCommandRepository(FakePool(cursor))

    rows = asyncio.run(repository.lease_pending(limit=5, worker_id="worker-a", lease_seconds=60))

    assert "FOR UPDATE SKIP LOCKED" in cursor.executed[0][0]
    assert "status IN ('PENDING', 'RETRYABLE')" in cursor.executed[0][0]
    assert "lease_expires_at < NOW(6)" in cursor.executed[0][0]
    assert cursor.executed[1][1] == (60, "worker-a", 1)
    assert rows[0]["payload_json"] == {"ok": True}


def test_external_command_lease_pending_returns_empty_when_no_available_rows():
    import asyncio

    class EmptyLeaseCursor(FakeCursor):
        async def execute(self, sql, args):
            self.sql = sql
            self.args = args

        async def fetchall(self):
            return []

    cursor = EmptyLeaseCursor(rowcount=0)
    repository = ExternalCommandRepository(FakePool(cursor))

    rows = asyncio.run(repository.lease_pending(limit=5, worker_id="worker-b", lease_seconds=60))

    assert rows == []
    assert "locked_by IS NULL OR lease_expires_at IS NULL OR lease_expires_at < NOW(6)" in cursor.sql


def test_external_command_release_and_processing_failure_and_recover_sql():
    import asyncio

    async def run_methods():
        cursor = FakeCursor(rowcount=2)
        repository = ExternalCommandRepository(FakePool(cursor))
        await repository.release_lease(1)
        release_sql = cursor.sql
        await repository.mark_processing_failed(2, "temporary", max_retries=3)
        failed_sql = cursor.sql
        failed_args = cursor.args
        recovered = await repository.recover_expired_leases()
        recover_sql = cursor.sql
        return release_sql, failed_sql, failed_args, recover_sql, recovered

    release_sql, failed_sql, failed_args, recover_sql, recovered = asyncio.run(run_methods())

    assert "leased_at = NULL" in release_sql
    assert "status = CASE WHEN retry_count + 1 >= %s THEN 'FAILED' ELSE 'RETRYABLE' END" in failed_sql
    assert failed_args == (3, "temporary", 2)
    assert "status IN ('PENDING', 'RETRYABLE')" in recover_sql
    assert "lease_expires_at < NOW(6)" in recover_sql
    assert recovered == 2


def make_external_command_result() -> dict:
    return {
        "external_command_id": 55,
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 11,
        "command_type": "backend.query",
        "result_type": "backend.query.mock_result",
        "result_json": {"status": "MOCKED"},
    }


async def run_external_result_insert_idempotent(rowcount: int):
    cursor = FakeCursor(rowcount=rowcount)
    cursor.lastrowid = 456
    repository = ExternalCommandResultRepository(FakePool(cursor))

    result = await repository.insert_idempotent(make_external_command_result())

    return result, cursor


def test_external_command_result_dedup_key_is_stable_for_payload_order():
    first = build_external_command_result_dedup_key(
        tenant_id="default",
        conversation_id="livechat:chat-1",
        external_command_id=55,
        command_type="backend.query",
        result_type="backend.query.mock_result",
        result={"b": 2, "a": 1},
    )
    second = build_external_command_result_dedup_key(
        tenant_id="default",
        conversation_id="livechat:chat-1",
        external_command_id=55,
        command_type="backend.query",
        result_type="backend.query.mock_result",
        result={"a": 1, "b": 2},
    )

    assert first == second
    assert first.startswith("default:livechat:chat-1:55:backend.query:backend.query.mock_result:")


def test_external_command_result_insert_idempotent_uses_dedup_key():
    import asyncio

    result, cursor = asyncio.run(run_external_result_insert_idempotent(rowcount=1))

    assert "INSERT INTO external_command_results" in cursor.sql
    assert "ON DUPLICATE KEY UPDATE id = id" in cursor.sql
    assert cursor.args[6] == "backend.query"
    assert cursor.args[12].startswith("default:livechat:chat-1:55:backend.query:backend.query.mock_result:")
    assert result == {"inserted": True, "duplicate": False, "id": 456}


def test_external_command_result_insert_idempotent_reports_duplicate():
    import asyncio

    result, _cursor = asyncio.run(run_external_result_insert_idempotent(rowcount=0))

    assert result == {"inserted": False, "duplicate": True, "id": None}


def test_external_command_result_fetch_pending_filters_pending_by_created_at():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 1,
                    "result_json": '{"ok": true}',
                    "status": "PENDING",
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = ExternalCommandResultRepository(FakePool(cursor))

    rows = asyncio.run(repository.fetch_pending(limit=5))

    assert "FROM external_command_results" in cursor.sql
    assert "WHERE status = 'PENDING'" in cursor.sql
    assert "ORDER BY created_at ASC" in cursor.sql
    assert cursor.args == (5,)
    assert rows[0]["result_json"] == {"ok": True}


def test_external_command_result_mark_status_methods():
    import asyncio

    async def run_marks():
        cursor = FakeCursor(rowcount=1)
        repository = ExternalCommandResultRepository(FakePool(cursor))

        await repository.mark_processed(1)
        processed_sql = cursor.sql
        await repository.mark_failed(2, "bad")
        failed_sql = cursor.sql
        failed_args = cursor.args
        await repository.mark_retryable(3, "temporary")
        retryable_sql = cursor.sql
        retryable_args = cursor.args
        return processed_sql, failed_sql, failed_args, retryable_sql, retryable_args

    processed_sql, failed_sql, failed_args, retryable_sql, retryable_args = asyncio.run(run_marks())

    assert "SET status = 'PROCESSED'" in processed_sql
    assert "processed_at = NOW(6)" in processed_sql
    assert "locked_by = NULL" in processed_sql
    assert "SET status = 'FAILED'" in failed_sql
    assert "last_error = %s" in failed_sql
    assert failed_args == ("bad", 2)
    assert "SET status = 'RETRYABLE'" in retryable_sql
    assert "last_error = %s" in retryable_sql
    assert retryable_args == ("temporary", 3)


def test_external_command_result_lease_pending_uses_skip_locked_and_returns_rows():
    import asyncio

    class LeaseCursor(FakeCursor):
        def __init__(self) -> None:
            super().__init__(rowcount=1)
            self.executed = []

        async def execute(self, sql, args):
            self.sql = sql
            self.args = args
            self.executed.append((sql, args))

        async def fetchall(self):
            if self.sql.strip().startswith("SELECT id\n        FROM external_command_results"):
                return [{"id": 7}]
            return [
                {
                    "id": 7,
                    "result_json": '{"ok": true}',
                    "status": "PENDING",
                    "locked_by": "consumer-a",
                }
            ]

    cursor = LeaseCursor()
    repository = ExternalCommandResultRepository(FakePool(cursor))

    rows = asyncio.run(repository.lease_pending(limit=5, worker_id="consumer-a", lease_seconds=60))

    assert "FROM external_command_results" in cursor.executed[0][0]
    assert "FOR UPDATE SKIP LOCKED" in cursor.executed[0][0]
    assert "status IN ('PENDING', 'RETRYABLE')" in cursor.executed[0][0]
    assert cursor.executed[1][1] == (60, "consumer-a", 7)
    assert rows[0]["result_json"] == {"ok": True}


def test_external_command_result_release_and_processing_failure_and_recover_sql():
    import asyncio

    async def run_methods():
        cursor = FakeCursor(rowcount=2)
        repository = ExternalCommandResultRepository(FakePool(cursor))
        await repository.release_lease(1)
        release_sql = cursor.sql
        await repository.mark_processing_failed(2, "temporary", max_retries=3)
        failed_sql = cursor.sql
        failed_args = cursor.args
        recovered = await repository.recover_expired_leases()
        recover_sql = cursor.sql
        return release_sql, failed_sql, failed_args, recover_sql, recovered

    release_sql, failed_sql, failed_args, recover_sql, recovered = asyncio.run(run_methods())

    assert "leased_at = NULL" in release_sql
    assert "status = CASE WHEN retry_count + 1 >= %s THEN 'FAILED' ELSE 'RETRYABLE' END" in failed_sql
    assert failed_args == (3, "temporary", 2)
    assert "status IN ('PENDING', 'RETRYABLE')" in recover_sql
    assert "lease_expires_at < NOW(6)" in recover_sql
    assert recovered == 2


def test_external_result_transaction_repository_commits_state_outbox_and_processed_in_order():
    import asyncio

    calls = []

    class ConversationRepo:
        async def get_or_create_on_connection(self, conn, chat_id: str, thread_id: str | None = None):
            calls.append("get_conversation")
            return {"conversation_id": "livechat:chat-1"}

        async def update_workflow_state_on_connection(self, conn, conversation_id: str, graph_state: dict):
            calls.append("update_state")

    class OutboundRepo:
        async def insert_idempotent_on_connection(self, conn, message: dict):
            calls.append("insert_outbound")
            return {"inserted": True}

    class ResultRepo:
        async def mark_processed_on_connection(self, conn, result_id: int):
            calls.append("mark_processed")

    pool = FakePool(FakeCursor(rowcount=1))
    repository = ExternalResultTransactionRepository(
        pool,
        conversation_repository=ConversationRepo(),
        outbound_repository=OutboundRepo(),
        result_repository=ResultRepo(),
    )

    asyncio.run(
        repository.process_result_transactionally(
            {
                "id": 7,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "conversation_id": "livechat:chat-1",
            },
            graph_state={"workflow_stage": "waiting_backend", "slot_memory": {}},
            outbound_messages=[{"conversation_id": "livechat:chat-1"}],
        )
    )

    assert calls == ["get_conversation", "update_state", "insert_outbound", "mark_processed"]
    assert pool.last_connection.committed is True
    assert pool.last_connection.rolled_back is False


def test_external_result_transaction_repository_rolls_back_before_processed_on_outbox_failure():
    import asyncio

    calls = []

    class ConversationRepo:
        async def get_or_create_on_connection(self, conn, chat_id: str, thread_id: str | None = None):
            calls.append("get_conversation")
            return {"conversation_id": "livechat:chat-1"}

        async def update_workflow_state_on_connection(self, conn, conversation_id: str, graph_state: dict):
            calls.append("update_state")

    class OutboundRepo:
        async def insert_idempotent_on_connection(self, conn, message: dict):
            calls.append("insert_outbound")
            raise RuntimeError("outbox failed")

    class ResultRepo:
        async def mark_processed_on_connection(self, conn, result_id: int):
            calls.append("mark_processed")

    pool = FakePool(FakeCursor(rowcount=1))
    repository = ExternalResultTransactionRepository(
        pool,
        conversation_repository=ConversationRepo(),
        outbound_repository=OutboundRepo(),
        result_repository=ResultRepo(),
    )

    try:
        asyncio.run(
            repository.process_result_transactionally(
                {
                    "id": 7,
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "conversation_id": "livechat:chat-1",
                },
                graph_state={"workflow_stage": "waiting_backend", "slot_memory": {}},
                outbound_messages=[{"conversation_id": "livechat:chat-1"}],
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "outbox failed"
    else:
        raise AssertionError("expected outbox failure")

    assert calls == ["get_conversation", "update_state", "insert_outbound"]
    assert pool.last_connection.committed is False
    assert pool.last_connection.rolled_back is True
