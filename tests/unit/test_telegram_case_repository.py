import asyncio

from app.db.telegram_repositories import TelegramCaseRepository


class RecordingCursor:
    def __init__(self, *, rows=None, row=None, lastrowid=1):
        self.rows = rows or []
        self.row = row
        self.lastrowid = lastrowid
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, sql, args=None):
        self.calls.append((sql, args))

    async def executemany(self, sql, args):
        self.calls.append((sql, args))

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.row


class RecordingConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def cursor(self, *args):
        return self.cursor_instance

    async def begin(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class RecordingPool:
    def __init__(self, cursor):
        self.connection = RecordingConnection(cursor)

    def acquire(self):
        return self.connection


RecordingConnection.__aenter__ = lambda self: _async_value(self)
RecordingConnection.__aexit__ = lambda self, *args: _async_value(None)


async def _async_value(value):
    return value


def _case_created_row():
    return {
        "tenant_id": "tenant-a",
        "conversation_id": "livechat:chat-1:thread-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 5,
        "external_command_id": 7,
    }


def _case_created_result():
    return {
        "intent": "withdrawal_missing",
        "active_workflow": "withdrawal_missing",
        "target_chat_id": "-1001",
        "message_thread_id": 9,
        "telegram_message_id": 123,
    }


def test_case_candidate_query_is_money_chat_scoped_and_excludes_current_thread():
    cursor = RecordingCursor(rows=[])
    repository = TelegramCaseRepository(RecordingPool(cursor))

    result = asyncio.run(repository.list_money_case_candidates("tenant-a", "chat-1", "thread-2"))

    assert result == []
    sql, args = cursor.calls[0]
    assert "c.intent IN ('deposit_missing', 'withdrawal_missing')" in sql
    assert "COALESCE(c.current_thread_id, c.thread_id, '') <> %s" in sql
    assert args == ("tenant-a", "chat-1", "thread-2")


def test_case_candidate_query_persists_lazy_legacy_status_normalization():
    cursor = RecordingCursor(
        rows=[
            {
                "id": 8,
                "intent": "withdrawal_missing",
                "status": "created",
                "slot_memory": "{}",
            }
        ]
    )
    repository = TelegramCaseRepository(RecordingPool(cursor))

    result = asyncio.run(repository.list_money_case_candidates("tenant-a", "chat-1", "thread-2"))

    assert result[0]["status"] == "awaiting_review"
    update_sql, update_args = cursor.calls[1]
    assert "UPDATE telegram_cases SET status" in update_sql
    assert update_args == [("awaiting_review", 8)]


def test_mark_followup_sending_activates_latest_livechat_route():
    cursor = RecordingCursor(
        row={
            "telegram_case_id": 8,
            "source_conversation_id": "livechat:chat-1:thread-new",
            "source_thread_id": "thread-new",
        }
    )
    repository = TelegramCaseRepository(RecordingPool(cursor))

    asyncio.run(repository.mark_followup_sending(81))

    sql_text = "\n".join(sql for sql, _args in cursor.calls)
    assert "SET status = 'sending'" in sql_text
    assert "SET current_conversation_id = %s, current_thread_id = %s" in sql_text
    assert cursor.calls[-1][1] == ("livechat:chat-1:thread-new", "thread-new", 8)


def test_case_created_sync_only_normalizes_legacy_created_status():
    cursor = RecordingCursor(lastrowid=42)
    repository = TelegramCaseRepository(RecordingPool(cursor))

    result = asyncio.run(
        repository._upsert_case_created_on_connection(
            RecordingConnection(cursor), _case_created_row(), _case_created_result()
        )
    )

    assert result == 42
    sql, _args = cursor.calls[0]
    assert "WHEN status = 'created' THEN 'awaiting_review'" in sql
    assert "status = VALUES(status)" not in sql


def test_staff_reply_lookup_uses_current_livechat_route():
    cursor = RecordingCursor(
        row={
            "id": 8,
            "tenant_id": "default",
            "conversation_id": "livechat:chat-1:thread-new",
            "chat_id": "chat-1",
            "thread_id": "thread-new",
            "telegram_message_thread_id": 12,
            "slot_memory": '{"last_reply_language":"en"}',
        }
    )
    repository = TelegramCaseRepository(RecordingPool(cursor))

    case = asyncio.run(repository.find_by_reply_message("-1001", 123, 12))

    sql, _args = cursor.calls[0]
    assert "COALESCE(c.current_conversation_id, c.conversation_id) AS conversation_id" in sql
    assert "COALESCE(c.current_thread_id, c.thread_id) AS thread_id" in sql
    assert "s.conversation_id = COALESCE(c.current_conversation_id, c.conversation_id)" in sql
    assert case["conversation_id"] == "livechat:chat-1:thread-new"
    assert case["thread_id"] == "thread-new"
    assert case["reply_language"] == "en"


def test_record_edited_append_only_records_new_attachment_messages():
    class FakeRepository(TelegramCaseRepository):
        def __init__(self) -> None:
            self.inserted = []
            self.pool = FakePool()

        async def find_by_reply_message(self, telegram_chat_id, reply_to_message_id, message_thread_id=None):
            return {"id": 77}

        async def _insert_case_message_on_connection(
            self,
            conn,
            telegram_case_id,
            telegram_chat_id,
            telegram_message_thread_id,
            telegram_message_id,
            message_kind,
        ):
            self.inserted.append(
                {
                    "telegram_case_id": telegram_case_id,
                    "telegram_chat_id": telegram_chat_id,
                    "telegram_message_id": telegram_message_id,
                    "message_kind": message_kind,
                }
            )

    class FakePool:
        def acquire(self):
            return FakeConnection()

    class FakeConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    repository = FakeRepository()

    result = asyncio.run(
        repository.record_append_message(
            {"result_type": "telegram.append_to_case.result"},
            {
                "status": "edited",
                "telegram_message_id": 123,
                "reply_to_message_id": 123,
                "target_chat_id": "-100test",
                "message_thread_id": None,
                "attachment_results": [{"result": {"message_id": 124}}],
            },
        )
    )

    assert result == {"telegram_case_id": 77, "telegram_message_id": 123}
    assert repository.inserted == [
        {
            "telegram_case_id": 77,
            "telegram_chat_id": "-100test",
            "telegram_message_id": 124,
            "message_kind": "attachment",
        }
    ]
