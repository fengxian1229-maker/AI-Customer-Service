from datetime import datetime

from app.db.repositories import (
    ConversationMessageRepository,
    ExternalCommandRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    FaqSmokeReadRepository,
    GraphCheckpointRunRepository,
    GraphRunErrorRepository,
    InboundEventRepository,
    KnowledgeDocumentRepository,
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
        self.fetchall_result = []
        self.fetchone_result = None

    async def execute(self, sql, args):
        self.sql = sql
        self.args = args

    async def fetchall(self):
        return self.fetchall_result

    async def fetchone(self):
        return self.fetchone_result

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


async def run_fetch_unprocessed_with_datetime():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 11,
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "event_id": "event-1",
                    "event_type": "message",
                    "standard_event_type": "MESSAGE_CREATED",
                    "author_id": "user-1",
                    "sender_role": "external",
                    "occurred_at": datetime(2026, 6, 24, 0, 0, 0),
                    "dedup_key": "dedup:event-1",
                    "payload_json": '{"event":{"text":"hello"}}',
                    "raw_action": "polling.event",
                    "source": "polling_fallback",
                    "organization_id": None,
                    "ignored": 0,
                    "ignore_reason": None,
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = InboundEventRepository(FakePool(cursor))
    rows = await repository.fetch_unprocessed(limit=1)
    return rows, cursor


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


async def run_outbound_fetch_pending():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return []

    cursor = FetchCursor(rowcount=0)
    repository = OutboundMessageRepository(FakePool(cursor))

    rows = await repository.fetch_pending(limit=5)

    return rows, cursor


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


def test_inbound_fetch_unprocessed_normalizes_datetime_occurred_at_to_string():
    import asyncio

    rows, cursor = asyncio.run(run_fetch_unprocessed_with_datetime())

    assert "SELECT id, chat_id, thread_id" in cursor.sql
    assert rows[0]["occurred_at"] == "2026-06-24 00:00:00.000000"
    assert rows[0]["payload_json"] == {"event": {"text": "hello"}}


def test_outbound_insert_idempotent_uses_inbound_action_duplicate_key():
    import asyncio

    result, cursor = asyncio.run(run_outbound_insert_idempotent(rowcount=1))

    assert "ON DUPLICATE KEY UPDATE id = id" in cursor.sql
    assert "dedup_key" in cursor.sql
    assert "block_index" in cursor.sql
    assert "message_kind" in cursor.sql
    assert "command_type" in cursor.sql
    assert "inbound_event_id" in cursor.sql
    assert "action_type" in cursor.sql
    assert cursor.args[8] == "default:livechat:chat-1:11:send_event"
    assert cursor.args[9] is None
    assert cursor.args[10] == "text"
    assert cursor.args[11] == "send_event"
    assert result == {"inserted": True, "duplicate": False, "id": 99}


def test_outbound_fetch_pending_qualifies_status_in_joined_query():
    import asyncio

    rows, cursor = asyncio.run(run_outbound_fetch_pending())
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "FROM outbound_messages m" in normalized_sql
    assert "LEFT JOIN conversation_states c ON c.conversation_id = m.conversation_id" in normalized_sql
    assert "WHERE m.status = 'PENDING'" in normalized_sql
    assert "WHERE status = 'PENDING'" not in normalized_sql
    assert cursor.args == (5,)


def test_outbound_insert_idempotent_accepts_faq_multiblock_dedup_fields():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    cursor.lastrowid = 101
    repository = OutboundMessageRepository(FakePool(cursor))
    message = {
        **make_outbound_message(),
        "action_type": "livechat.send_image",
        "message_type": "image",
        "payload_json": {"asset_key": "deposit_howto"},
        "dedup_key": "default:livechat:chat-1:11:faq_block:0:image:deposit_howto",
        "block_index": 0,
        "message_kind": "image",
        "command_type": "livechat.send_image",
    }

    result = asyncio.run(repository.insert_idempotent(message))

    assert cursor.args[8] == "default:livechat:chat-1:11:faq_block:0:image:deposit_howto"
    assert cursor.args[9] == 0
    assert cursor.args[10] == "image"
    assert cursor.args[11] == "livechat.send_image"
    assert result == {"inserted": True, "duplicate": False, "id": 101}


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


async def run_external_fetch_pending():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return []

    cursor = FetchCursor(rowcount=0)
    repository = ExternalCommandRepository(FakePool(cursor))
    rows = await repository.fetch_pending(limit=3)
    return rows, cursor


async def run_external_result_fetch_pending():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return []

    cursor = FetchCursor(rowcount=0)
    repository = ExternalCommandResultRepository(FakePool(cursor))
    rows = await repository.fetch_pending(limit=4)
    return rows, cursor


async def run_graph_run_error_insert(rowcount: int):
    cursor = FakeCursor(rowcount=rowcount)
    cursor.lastrowid = 77
    repository = GraphRunErrorRepository(FakePool(cursor))

    result = await repository.insert(
        {
            "conversation_id": "livechat:chat-1",
            "inbound_event_id": 11,
            "graph_thread_id": "thread-graph-1",
            "node_name": "router",
            "error_type": "RuntimeError",
            "error_message": "graph exploded",
            "retryable": 0,
            "state_snapshot": {"conversation_id": "livechat:chat-1"},
        }
    )

    return result, cursor


async def run_graph_checkpoint_run_insert(rowcount: int):
    cursor = FakeCursor(rowcount=rowcount)
    cursor.lastrowid = 66
    repository = GraphCheckpointRunRepository(FakePool(cursor))
    result = await repository.insert_run(
        {
            "conversation_id": "livechat:chat-1",
            "graph_thread_id": "livechat:chat-1",
            "checkpoint_mode": "memory",
            "status": "CREATED",
            "inbound_event_id": 11,
            "latest_checkpoint_id": None,
            "metadata_json": {"checkpoint_mode": "memory", "node_count": 5, "api_key": "skip-me"},
        }
    )
    return result, cursor


async def run_graph_checkpoint_run_list_filtered():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 66,
                    "conversation_id": "livechat:chat-1",
                    "graph_thread_id": "livechat:chat-1",
                    "checkpoint_mode": "mysql",
                    "status": "FAILED",
                    "inbound_event_id": 11,
                    "latest_checkpoint_id": "cp-1",
                    "error_type": "RuntimeError",
                    "error_message": "boom",
                    "metadata_json": '{"checkpoint_mode":"mysql","node_count":5}',
                    "created_at": "2026-06-26 00:00:00.000000",
                    "updated_at": "2026-06-26 00:00:10.000000",
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = GraphCheckpointRunRepository(FakePool(cursor))
    rows = await repository.list_runs(
        conversation_id="livechat:chat-1",
        graph_thread_id="livechat:chat-1",
        inbound_event_id=11,
        status="FAILED",
        created_after="2026-06-25 00:00:00",
        created_before="2026-06-27 00:00:00",
        limit=5,
    )
    return rows, cursor


async def run_graph_run_error_list_filtered():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 77,
                    "conversation_id": "livechat:chat-1",
                    "inbound_event_id": 11,
                    "graph_thread_id": "livechat:chat-1",
                    "node_name": "intent_router_node",
                    "error_type": "RuntimeError",
                    "error_message": "boom",
                    "retryable": 0,
                    "state_snapshot": '{"conversation_id":"livechat:chat-1"}',
                    "created_at": "2026-06-26 00:00:00.000000",
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = GraphRunErrorRepository(FakePool(cursor))
    rows = await repository.list_errors(
        conversation_id="livechat:chat-1",
        graph_thread_id="livechat:chat-1",
        inbound_event_id=11,
        status="FAILED",
        created_after="2026-06-25 00:00:00",
        created_before="2026-06-27 00:00:00",
        limit=5,
    )
    return rows, cursor


async def run_graph_run_error_fetch_retryable(limit: int = 20):
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 77,
                    "conversation_id": "livechat:chat-1",
                    "inbound_event_id": 11,
                    "graph_thread_id": "livechat:chat-1",
                    "node_name": "router",
                    "error_type": "TimeoutError",
                    "error_message": "timed out",
                    "retryable": 1,
                    "state_snapshot": '{"conversation_id":"livechat:chat-1"}',
                    "created_at": "2026-06-26 00:00:00.000000",
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = GraphRunErrorRepository(FakePool(cursor))

    rows = await repository.fetch_retryable(limit=limit)

    return rows, cursor


async def run_faq_smoke_latest_outbound():
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 7,
                    "conversation_id": "livechat:chat-1",
                    "inbound_event_id": 11,
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "action_type": "send_event",
                    "command_type": "livechat.send_text",
                    "message_type": "text",
                    "message_kind": "text",
                    "block_index": None,
                    "status": "SENT",
                    "retry_count": 0,
                    "last_error": None,
                    "sent_at": datetime(2026, 6, 27, 1, 2, 3),
                    "created_at": datetime(2026, 6, 27, 1, 2, 2),
                    "payload_json": '{"text":"怎么存款？","access_token":"hidden"}',
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = FaqSmokeReadRepository(FakePool(cursor))
    rows = await repository.latest_outbound(
        conversation_id="livechat:chat-1",
        chat_id="chat-1",
        inbound_event_id=11,
        limit=5,
    )
    return rows, cursor


async def run_faq_smoke_latest_inbound(**kwargs):
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return []

    cursor = FetchCursor(rowcount=0)
    repository = FaqSmokeReadRepository(FakePool(cursor))
    rows = await repository.latest_inbound(**kwargs)
    return rows, cursor


async def run_faq_smoke_latest_checkpoints(**kwargs):
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return []

    cursor = FetchCursor(rowcount=0)
    repository = FaqSmokeReadRepository(FakePool(cursor))
    rows = await repository.latest_checkpoints(**kwargs)
    return rows, cursor


async def run_faq_smoke_latest_errors(**kwargs):
    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return []

    cursor = FetchCursor(rowcount=0)
    repository = FaqSmokeReadRepository(FakePool(cursor))
    rows = await repository.latest_errors(**kwargs)
    return rows, cursor


async def run_faq_smoke_summary_capture(**kwargs):
    calls = []

    class SummaryRepository(FaqSmokeReadRepository):
        async def latest_inbound(self, *args, **method_kwargs):
            calls.append(("latest_inbound", args, method_kwargs))
            return []

        async def latest_outbound(self, *args, **method_kwargs):
            calls.append(("latest_outbound", args, method_kwargs))
            return []

        async def latest_conversation(self, *args, **method_kwargs):
            calls.append(("latest_conversation", args, method_kwargs))
            return []

        async def latest_checkpoints(self, *args, **method_kwargs):
            calls.append(("latest_checkpoints", args, method_kwargs))
            return []

        async def latest_errors(self, *args, **method_kwargs):
            calls.append(("latest_errors", args, method_kwargs))
            return []

    repository = SummaryRepository(FakePool(FakeCursor(rowcount=0)))
    summary = await repository.summary(**kwargs)
    return summary, calls


async def run_faq_smoke_summary():
    class SummaryRepository(FaqSmokeReadRepository):
        async def latest_inbound(self, *args, **kwargs):
            return [{"processed": 1, "ignored": 0}]

        async def latest_outbound(self, *args, **kwargs):
            return [{"id": 7, "status": "SENT", "last_error": None}]

        async def latest_conversation(self, *args, **kwargs):
            return [{"sender_role": "customer"}, {"sender_role": "assistant"}]

        async def latest_checkpoints(self, *args, **kwargs):
            return [{"status": "SUCCEEDED"}]

        async def latest_errors(self, *args, **kwargs):
            return []

    repository = SummaryRepository(FakePool(FakeCursor(rowcount=0)))
    return await repository.summary(limit=20)


def make_conversation_message(**overrides) -> dict:
    base = {
        "conversation_id": "livechat:chat-1",
        "tenant_id": "default",
        "channel_type": "livechat",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 11,
        "outbound_message_id": None,
        "external_command_result_id": None,
        "sender_role": "customer",
        "message_type": "text",
        "text_content": "hola",
        "attachment_refs": [],
        "source": "inbound_event",
        "occurred_at": "2026-06-24 00:00:00.000000",
    }
    base.update(overrides)
    return base


async def run_conversation_message_insert(rowcount: int, message: dict | None = None):
    cursor = FakeCursor(rowcount=rowcount)
    cursor.lastrowid = 88
    repository = ConversationMessageRepository(FakePool(cursor))

    result = await repository.insert_idempotent(message or make_conversation_message())

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


def test_external_command_fetch_pending_keeps_unaliased_status():
    import asyncio

    rows, cursor = asyncio.run(run_external_fetch_pending())
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "FROM external_commands" in normalized_sql
    assert "WHERE status = 'PENDING'" in normalized_sql
    assert "m.status" not in normalized_sql
    assert cursor.args == (3,)


def test_external_command_result_fetch_pending_keeps_unaliased_status():
    import asyncio

    rows, cursor = asyncio.run(run_external_result_fetch_pending())
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "FROM external_command_results" in normalized_sql
    assert "WHERE status = 'PENDING'" in normalized_sql
    assert "m.status" not in normalized_sql
    assert cursor.args == (4,)


def test_graph_run_error_insert_writes_json_snapshot():
    import asyncio

    result, cursor = asyncio.run(run_graph_run_error_insert(rowcount=1))

    assert "INSERT INTO graph_run_errors" in cursor.sql
    assert "state_snapshot" in cursor.sql
    assert cursor.args[0] == "livechat:chat-1"
    assert cursor.args[1] == 11
    assert cursor.args[4] == "RuntimeError"
    assert cursor.args[6] == 0
    assert cursor.args[7] == '{"conversation_id":"livechat:chat-1"}'
    assert result == 77


def test_graph_checkpoint_run_insert_writes_json_metadata():
    import asyncio

    result, cursor = asyncio.run(run_graph_checkpoint_run_insert(rowcount=1))

    assert "INSERT INTO graph_checkpoint_runs" in cursor.sql
    assert "metadata_json" in cursor.sql
    assert cursor.args[0] == "livechat:chat-1"
    assert cursor.args[2] == "memory"
    assert cursor.args[5] is None
    assert cursor.args[6] == '{"checkpoint_mode":"memory","node_count":5}'
    assert result == 66


def test_graph_checkpoint_run_mark_succeeded_updates_status_and_checkpoint_id():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    repository = GraphCheckpointRunRepository(FakePool(cursor))

    asyncio.run(repository.mark_succeeded(66, latest_checkpoint_id="cp-1"))

    assert "UPDATE graph_checkpoint_runs" in cursor.sql
    assert "SET status = 'SUCCEEDED'" in cursor.sql
    assert cursor.args == ("cp-1", 66)


def test_graph_checkpoint_run_mark_failed_writes_error_fields():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    repository = GraphCheckpointRunRepository(FakePool(cursor))

    asyncio.run(repository.mark_failed(66, RuntimeError("checkpoint failed")))

    assert "UPDATE graph_checkpoint_runs" in cursor.sql
    assert "SET status = 'FAILED'" in cursor.sql
    assert cursor.args == ("RuntimeError", "checkpoint failed", 66)


def test_graph_run_error_fetch_retryable_reads_graph_run_errors_and_loads_snapshot():
    import asyncio

    rows, cursor = asyncio.run(run_graph_run_error_fetch_retryable(limit=5))

    assert "FROM graph_run_errors" in cursor.sql
    assert "WHERE retryable = 1" in cursor.sql
    assert cursor.args == (5,)
    assert rows[0]["state_snapshot"] == {"conversation_id": "livechat:chat-1"}


def test_faq_smoke_latest_outbound_uses_parameterized_filters_and_text_summary():
    import asyncio

    rows, cursor = asyncio.run(run_faq_smoke_latest_outbound())
    normalized_sql = " ".join(cursor.sql.split())

    assert "FROM outbound_messages" in normalized_sql
    assert "conversation_id = %s" in normalized_sql
    assert "chat_id = %s" in normalized_sql
    assert "inbound_event_id = %s" in normalized_sql
    assert cursor.args == ("livechat:chat-1", "chat-1", 11, 5)
    assert rows[0]["text"] == "怎么存款？"
    assert "payload_json" not in rows[0]
    assert "access_token" not in str(rows[0])
    assert rows[0]["sent_at"] == "2026-06-27 01:02:03.000000"


def test_faq_smoke_latest_inbound_maps_livechat_conversation_id_to_chat_id():
    import asyncio

    rows, cursor = asyncio.run(run_faq_smoke_latest_inbound(conversation_id="livechat:chat-1", limit=5))
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "FROM inbound_events" in normalized_sql
    assert "chat_id = %s" in normalized_sql
    assert "conversation_id" not in normalized_sql
    assert cursor.args == ("chat-1", 5)


def test_faq_smoke_latest_inbound_prefers_explicit_chat_id_over_conversation_id():
    import asyncio

    rows, cursor = asyncio.run(
        run_faq_smoke_latest_inbound(
            chat_id="chat-2",
            conversation_id="livechat:chat-1",
            limit=5,
        )
    )
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "chat_id = %s" in normalized_sql
    assert cursor.args == ("chat-2", 5)


def test_faq_smoke_latest_checkpoints_maps_chat_id_to_livechat_conversation_id():
    import asyncio

    rows, cursor = asyncio.run(run_faq_smoke_latest_checkpoints(chat_id="chat-1", limit=5))
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "FROM graph_checkpoint_runs" in normalized_sql
    assert "conversation_id = %s" in normalized_sql
    assert "chat_id" not in normalized_sql
    assert cursor.args == ("livechat:chat-1", 5)


def test_faq_smoke_latest_errors_maps_chat_id_to_livechat_conversation_id():
    import asyncio

    rows, cursor = asyncio.run(run_faq_smoke_latest_errors(chat_id="chat-1", limit=5))
    normalized_sql = " ".join(cursor.sql.split())

    assert rows == []
    assert "FROM graph_run_errors" in normalized_sql
    assert "conversation_id = %s" in normalized_sql
    assert "chat_id" not in normalized_sql
    assert cursor.args == ("livechat:chat-1", 5)


def test_faq_smoke_summary_passes_consistent_chat_scope_to_subqueries():
    import asyncio

    summary, calls = asyncio.run(run_faq_smoke_summary_capture(chat_id="chat-1", limit=5))

    assert summary["overall"]["ok"] is False
    assert calls == [
        ("latest_inbound", ("livechat:chat-1", "chat-1", None, 5), {}),
        ("latest_outbound", ("livechat:chat-1", "chat-1", None, 5), {}),
        ("latest_conversation", ("livechat:chat-1", "chat-1", None, 5), {}),
        ("latest_checkpoints", ("livechat:chat-1", "chat-1", None, 5), {}),
        ("latest_errors", ("livechat:chat-1", "chat-1", None, 5), {}),
    ]


def test_faq_smoke_summary_marks_ok_when_closed_loop_signals_exist():
    import asyncio

    summary = asyncio.run(run_faq_smoke_summary())

    assert summary["inbound"]["processed_count"] == 1
    assert summary["outbound"]["sent_count"] == 1
    assert summary["conversation_messages"]["has_customer_assistant_pair"] is True
    assert summary["checkpoints"]["succeeded_count"] == 1
    assert summary["errors"]["error_count"] == 0
    assert summary["overall"]["ok"] is True


def test_graph_checkpoint_run_fetch_recent_filters_by_conversation_and_loads_metadata():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 66,
                    "conversation_id": "livechat:chat-1",
                    "graph_thread_id": "livechat:chat-1",
                    "checkpoint_mode": "memory",
                    "status": "SUCCEEDED",
                    "inbound_event_id": 11,
                    "latest_checkpoint_id": "cp-1",
                    "error_type": None,
                    "error_message": None,
                    "metadata_json": '{"checkpoint_mode":"memory","node_count":5}',
                    "created_at": "2026-06-26 00:00:00.000000",
                    "updated_at": "2026-06-26 00:00:00",
                }
            ]

    cursor = FetchCursor(rowcount=1)
    repository = GraphCheckpointRunRepository(FakePool(cursor))

    rows = asyncio.run(repository.fetch_recent("livechat:chat-1", limit=20))

    assert "FROM graph_checkpoint_runs" in cursor.sql
    assert "WHERE conversation_id = %s" in cursor.sql
    assert cursor.args == ("livechat:chat-1", 20)
    assert rows[0]["metadata_json"] == {"checkpoint_mode": "memory", "node_count": 5}


def test_graph_checkpoint_run_list_runs_supports_multi_filter_query():
    import asyncio

    rows, cursor = asyncio.run(run_graph_checkpoint_run_list_filtered())

    assert "FROM graph_checkpoint_runs" in cursor.sql
    assert "conversation_id = %s" in cursor.sql
    assert "graph_thread_id = %s" in cursor.sql
    assert "inbound_event_id = %s" in cursor.sql
    assert "status = %s" in cursor.sql
    assert "created_at >= %s" in cursor.sql
    assert "created_at <= %s" in cursor.sql
    assert cursor.args == (
        "livechat:chat-1",
        "livechat:chat-1",
        11,
        "FAILED",
        "2026-06-25 00:00:00",
        "2026-06-27 00:00:00",
        5,
    )
    assert rows[0]["metadata_json"] == {"checkpoint_mode": "mysql", "node_count": 5}


def test_graph_run_error_list_errors_supports_multi_filter_query():
    import asyncio

    rows, cursor = asyncio.run(run_graph_run_error_list_filtered())

    assert "FROM graph_run_errors" in cursor.sql
    assert "conversation_id = %s" in cursor.sql
    assert "graph_thread_id = %s" in cursor.sql
    assert "inbound_event_id = %s" in cursor.sql
    assert "created_at >= %s" in cursor.sql
    assert "created_at <= %s" in cursor.sql
    assert cursor.args == (
        "livechat:chat-1",
        "livechat:chat-1",
        11,
        "2026-06-25 00:00:00",
        "2026-06-27 00:00:00",
        5,
    )
    assert rows[0]["state_snapshot"] == {"conversation_id": "livechat:chat-1"}


def test_conversation_message_insert_idempotent_for_inbound_message():
    import asyncio

    result, cursor = asyncio.run(run_conversation_message_insert(rowcount=1))

    assert "INSERT INTO conversation_messages" in cursor.sql
    assert "ON DUPLICATE KEY UPDATE id = id" in cursor.sql
    assert cursor.args[0] == "livechat:chat-1"
    assert cursor.args[5] == 11
    assert cursor.args[8] == "customer"
    assert cursor.args[10] == "hola"
    assert cursor.args[11] == "[]"
    assert result == {"inserted": True, "duplicate": False, "id": 88}


def test_conversation_message_insert_idempotent_reports_duplicate():
    import asyncio

    result, _cursor = asyncio.run(run_conversation_message_insert(rowcount=0))

    assert result == {"inserted": False, "duplicate": True, "id": None}


def test_conversation_message_insert_supports_outbound_and_external_idempotency_keys():
    import asyncio

    outbound_result, outbound_cursor = asyncio.run(
        run_conversation_message_insert(
            rowcount=1,
            message=make_conversation_message(
                inbound_event_id=None,
                outbound_message_id=21,
                sender_role="assistant",
                source="sender_worker",
            ),
        )
    )
    external_result, external_cursor = asyncio.run(
        run_conversation_message_insert(
            rowcount=1,
            message=make_conversation_message(
                inbound_event_id=None,
                external_command_result_id=31,
                sender_role="backend",
                message_type="external_result",
                source="external_result_consumer",
            ),
        )
    )

    assert outbound_result["inserted"] is True
    assert outbound_cursor.args[6] == 21
    assert external_result["inserted"] is True
    assert external_cursor.args[7] == 31
    assert external_cursor.args[9] == "external_result"


def test_conversation_message_fetch_recent_returns_oldest_first_after_limit():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 3,
                    "conversation_id": "livechat:chat-1",
                    "sender_role": "assistant",
                    "message_type": "text",
                    "text_content": "third",
                    "attachment_refs": '[{"url":"https://cdn.example/file.png"}]',
                    "source": "sender_worker",
                    "created_at": "2026-06-24 00:00:03.000000",
                },
                {
                    "id": 2,
                    "conversation_id": "livechat:chat-1",
                    "sender_role": "customer",
                    "message_type": "text",
                    "text_content": "second",
                    "attachment_refs": "[]",
                    "source": "inbound_event",
                    "created_at": "2026-06-24 00:00:02.000000",
                },
            ]

    cursor = FetchCursor(rowcount=2)
    repository = ConversationMessageRepository(FakePool(cursor))

    rows = asyncio.run(repository.fetch_recent("livechat:chat-1", limit=2))

    assert "FROM conversation_messages" in cursor.sql
    assert "ORDER BY created_at DESC, id DESC" in cursor.sql
    assert cursor.args == ("livechat:chat-1", 2)
    assert [row["text_content"] for row in rows] == ["second", "third"]
    assert rows[1]["attachment_refs"] == [{"url": "https://cdn.example/file.png"}]


def test_knowledge_document_insert_idempotent_uses_unique_key_upsert():
    import asyncio

    cursor = FakeCursor(rowcount=2)
    cursor.lastrowid = 42
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    result = asyncio.run(
        repository.insert_idempotent(
            {
                "tenant_id": "default",
                "kb_scope": "default",
                "title": "Bonus rules",
                "content": "奖金规则以活动页面说明为准。",
                "keywords": ["bonus"],
                "question_aliases": ["bonus rules"],
                "answer_blocks": [{"type": "text", "text": "奖金规则以活动页面说明为准。"}],
                "metadata_json": {"intent_id": "bonus_rules"},
                "language": "multi",
                "priority": 10,
                "enabled": True,
            }
        )
    )

    assert "ON DUPLICATE KEY UPDATE" in cursor.sql
    assert "content = VALUES(content)" in cursor.sql
    assert "keywords = VALUES(keywords)" in cursor.sql
    assert "question_aliases = VALUES(question_aliases)" in cursor.sql
    assert "answer_blocks = VALUES(answer_blocks)" in cursor.sql
    assert "metadata_json = VALUES(metadata_json)" in cursor.sql
    assert "language = VALUES(language)" in cursor.sql
    assert "priority = VALUES(priority)" in cursor.sql
    assert "enabled = VALUES(enabled)" in cursor.sql
    assert "updated_at = CURRENT_TIMESTAMP" in cursor.sql
    assert cursor.args[0:4] == ("default", "default", "Bonus rules", "奖金规则以活动页面说明为准。")
    assert '"奖金规则以活动页面说明为准。"' in cursor.args[6]
    assert result == {"inserted": False, "duplicate": True, "id": 42}


def test_knowledge_document_search_uses_parameterized_candidate_query_and_scores_matches():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 1,
                    "tenant_id": "default",
                    "kb_scope": "default",
                    "title": "Bonus rules",
                    "content": "奖金规则以活动页面说明为准。",
                    "keywords": '["bonus","rules"]',
                    "question_aliases": '["bonus rules","bonus terms"]',
                    "answer_blocks": '[{"type":"text","text":"奖金规则以活动页面说明为准。"}]',
                    "metadata_json": '{"intent_id":"bonus_rules"}',
                    "language": "en",
                    "priority": 20,
                },
                {
                    "id": 2,
                    "tenant_id": "default",
                    "kb_scope": "default",
                    "title": "Deposit guide",
                    "content": "how to deposit",
                    "keywords": '["deposit"]',
                    "question_aliases": None,
                    "answer_blocks": None,
                    "metadata_json": None,
                    "language": "en",
                    "priority": 10,
                },
            ]

    cursor = FetchCursor(rowcount=2)
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    rows = asyncio.run(repository.search("default", "bonus rules", limit=1))

    assert "FROM knowledge_documents" in cursor.sql
    assert "enabled = 1" in cursor.sql
    assert cursor.args == ("default", "default")
    assert rows[0]["id"] == 1
    assert rows[0]["keywords"] == ["bonus", "rules"]
    assert rows[0]["question_aliases"] == ["bonus rules", "bonus terms"]
    assert rows[0]["answer_blocks"] == [{"type": "text", "text": "奖金规则以活动页面说明为准。"}]
    assert rows[0]["metadata_json"] == {"intent_id": "bonus_rules"}
    assert rows[0]["score"] > 0
    assert rows[0]["matched_fields"] == ["title", "question_aliases", "keywords"]
    assert len(rows) == 1


def test_knowledge_document_search_returns_empty_without_match():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {
                    "id": 1,
                    "tenant_id": "default",
                    "kb_scope": "default",
                    "title": "Bonus rules",
                    "content": "奖金规则以活动页面说明为准。",
                    "keywords": '["bonus"]',
                    "question_aliases": None,
                    "answer_blocks": None,
                    "metadata_json": None,
                    "language": "en",
                    "priority": 20,
                },
            ]

    cursor = FetchCursor(rowcount=1)
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    rows = asyncio.run(repository.search("default", "withdrawal status", limit=3))

    assert rows == []


def test_knowledge_document_search_sorts_by_score_priority_and_id():
    import asyncio

    class FetchCursor(FakeCursor):
        async def fetchall(self):
            return [
                {"id": 3, "tenant_id": "default", "kb_scope": "default", "title": "bonus", "content": "bonus", "keywords": "[]", "question_aliases": None, "answer_blocks": None, "metadata_json": None, "language": None, "priority": 30},
                {"id": 1, "tenant_id": "default", "kb_scope": "default", "title": "bonus", "content": "", "keywords": '["bonus"]', "question_aliases": None, "answer_blocks": None, "metadata_json": None, "language": None, "priority": 10},
                {"id": 2, "tenant_id": "default", "kb_scope": "default", "title": "bonus", "content": "", "keywords": '["bonus"]', "question_aliases": None, "answer_blocks": None, "metadata_json": None, "language": None, "priority": 10},
            ]

    cursor = FetchCursor(rowcount=3)
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    rows = asyncio.run(repository.search("default", "bonus", limit=3))

    assert [row["id"] for row in rows] == [1, 2, 3]


def test_knowledge_document_list_documents_filters_tenant_scope_and_enabled():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    cursor.fetchall_result = [
        {
            "id": 1,
            "tenant_id": "default",
            "kb_scope": "default",
            "title": "Bonus rules",
            "content": "奖金规则以活动页面说明为准。",
            "keywords": '["bonus","rules"]',
            "question_aliases": '["bonus rules"]',
            "answer_blocks": '[{"type":"text","text":"奖金规则以活动页面说明为准。"}]',
            "metadata_json": '{"intent_id":"bonus_rules"}',
            "language": "multi",
            "priority": 10,
            "enabled": 1,
            "created_at": "2026-06-26 00:00:00.000000",
            "updated_at": "2026-06-26 00:00:00",
        }
    ]
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    rows = asyncio.run(repository.list_documents("default", kb_scope="default", enabled=True, limit=50))

    assert "WHERE tenant_id = %s" in cursor.sql
    assert "AND kb_scope = %s" in cursor.sql
    assert "AND enabled = %s" in cursor.sql
    assert "ORDER BY priority ASC, id ASC" in cursor.sql
    assert cursor.args == ("default", "default", 1, 50)
    assert rows[0]["keywords"] == ["bonus", "rules"]
    assert rows[0]["question_aliases"] == ["bonus rules"]
    assert rows[0]["answer_blocks"] == [{"type": "text", "text": "奖金规则以活动页面说明为准。"}]
    assert rows[0]["metadata_json"] == {"intent_id": "bonus_rules"}


def test_knowledge_document_list_documents_skips_enabled_filter_when_none():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    asyncio.run(repository.list_documents("default", kb_scope="default", enabled=None, limit=20))

    assert "AND enabled = %s" not in cursor.sql
    assert cursor.args == ("default", "default", 20)


def test_knowledge_document_get_by_title_uses_parameterized_query():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    cursor.fetchone_result = {
        "id": 1,
        "tenant_id": "default",
        "kb_scope": "default",
        "title": "Bonus rules",
        "content": "奖金规则以活动页面说明为准。",
        "keywords": '["bonus","rules"]',
        "question_aliases": '["bonus rules"]',
        "answer_blocks": '[{"type":"text","text":"奖金规则以活动页面说明为准。"}]',
        "metadata_json": '{"intent_id":"bonus_rules"}',
        "language": "multi",
        "priority": 10,
        "enabled": 1,
        "created_at": "2026-06-26 00:00:00.000000",
        "updated_at": "2026-06-26 00:00:00",
    }
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    row = asyncio.run(repository.get_by_title("default", "default", "Bonus rules"))

    assert "WHERE tenant_id = %s AND kb_scope = %s AND title = %s" in cursor.sql
    assert cursor.args == ("default", "default", "Bonus rules")
    assert row["keywords"] == ["bonus", "rules"]
    assert row["question_aliases"] == ["bonus rules"]
    assert row["answer_blocks"] == [{"type": "text", "text": "奖金规则以活动页面说明为准。"}]
    assert row["metadata_json"] == {"intent_id": "bonus_rules"}


def test_knowledge_document_set_enabled_uses_parameterized_update():
    import asyncio

    cursor = FakeCursor(rowcount=1)
    repository = KnowledgeDocumentRepository(FakePool(cursor))

    result = asyncio.run(repository.set_enabled("default", "default", "Bonus rules", False))

    assert "UPDATE knowledge_documents" in cursor.sql
    assert "SET enabled = %s" in cursor.sql
    assert cursor.args == (0, "default", "default", "Bonus rules")
    assert result == {"updated": True, "rowcount": 1}


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


def test_external_result_transaction_repository_writes_external_commands_in_same_transaction():
    import asyncio

    calls = []
    captured_commands = []

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

    class ExternalCommandRepo:
        async def insert_idempotent_on_connection(self, conn, command: dict):
            calls.append("insert_external_command")
            captured_commands.append(dict(command))
            return {"inserted": True, "duplicate": False, "id": 55}

    class ResultRepo:
        async def mark_processed_on_connection(self, conn, result_id: int):
            calls.append("mark_processed")

    pool = FakePool(FakeCursor(rowcount=1))
    repository = ExternalResultTransactionRepository(
        pool,
        conversation_repository=ConversationRepo(),
        outbound_repository=OutboundRepo(),
        external_command_repository=ExternalCommandRepo(),
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
            graph_state={"workflow_stage": "needs_user_supplement", "slot_memory": {}},
            outbound_messages=[{"conversation_id": "livechat:chat-1"}],
            external_commands=[
                {
                    "tenant_id": "default",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 11,
                    "command_type": "telegram.append_to_case",
                    "payload_json": {"text": "needs screenshot"},
                }
            ],
        )
    )

    assert calls == ["get_conversation", "update_state", "insert_outbound", "insert_external_command", "mark_processed"]
    assert captured_commands[0]["conversation_id"] == "livechat:chat-1"
    assert captured_commands[0]["command_type"] == "telegram.append_to_case"
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


def test_external_result_transaction_repository_rolls_back_on_state_external_command_and_processed_failures():
    import asyncio

    async def assert_failure(conversation_repo, outbound_repo, external_command_repo, result_repo, expected_calls, error):
        calls.clear()
        pool = FakePool(FakeCursor(rowcount=1))
        repository = ExternalResultTransactionRepository(
            pool,
            conversation_repository=conversation_repo,
            outbound_repository=outbound_repo,
            external_command_repository=external_command_repo,
            result_repository=result_repo,
        )
        try:
            await repository.process_result_transactionally(
                {
                    "id": 7,
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "conversation_id": "livechat:chat-1",
                },
                graph_state={"workflow_stage": "completed", "slot_memory": {}},
                outbound_messages=[{"conversation_id": "livechat:chat-1"}],
                external_commands=[
                    {
                        "tenant_id": "default",
                        "chat_id": "chat-1",
                        "thread_id": "thread-1",
                        "command_type": "backend.query",
                        "payload_json": {"next": True},
                    }
                ],
            )
        except RuntimeError as exc:
            assert str(exc) == error
        else:
            raise AssertionError(f"expected {error}")
        assert calls == expected_calls
        assert pool.last_connection.committed is False
        assert pool.last_connection.rolled_back is True

    calls = []

    class BaseConversationRepo:
        async def get_or_create_on_connection(self, conn, chat_id: str, thread_id: str | None = None):
            calls.append("get_conversation")
            return {"conversation_id": "livechat:chat-1"}

        async def update_workflow_state_on_connection(self, conn, conversation_id: str, graph_state: dict):
            calls.append("update_state")

    class FailingConversationRepo(BaseConversationRepo):
        async def update_workflow_state_on_connection(self, conn, conversation_id: str, graph_state: dict):
            calls.append("update_state")
            raise RuntimeError("state failed")

    class OutboundRepo:
        async def insert_idempotent_on_connection(self, conn, message: dict):
            calls.append("insert_outbound")
            return {"inserted": True}

    class ExternalCommandRepo:
        async def insert_idempotent_on_connection(self, conn, command: dict):
            calls.append("insert_external_command")
            return {"inserted": True, "duplicate": False, "id": 55}

    class FailingExternalCommandRepo:
        async def insert_idempotent_on_connection(self, conn, command: dict):
            calls.append("insert_external_command")
            raise RuntimeError("external command failed")

    class ResultRepo:
        async def mark_processed_on_connection(self, conn, result_id: int):
            calls.append("mark_processed")

    class FailingResultRepo:
        async def mark_processed_on_connection(self, conn, result_id: int):
            calls.append("mark_processed")
            raise RuntimeError("mark processed failed")

    asyncio.run(
        assert_failure(
            FailingConversationRepo(),
            OutboundRepo(),
            ExternalCommandRepo(),
            ResultRepo(),
            ["get_conversation", "update_state"],
            "state failed",
        )
    )
    asyncio.run(
        assert_failure(
            BaseConversationRepo(),
            OutboundRepo(),
            FailingExternalCommandRepo(),
            ResultRepo(),
            ["get_conversation", "update_state", "insert_outbound", "insert_external_command"],
            "external command failed",
        )
    )
    asyncio.run(
        assert_failure(
            BaseConversationRepo(),
            OutboundRepo(),
            ExternalCommandRepo(),
            FailingResultRepo(),
            ["get_conversation", "update_state", "insert_outbound", "insert_external_command", "mark_processed"],
            "mark processed failed",
        )
    )
