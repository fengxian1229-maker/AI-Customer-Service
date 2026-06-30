import asyncio

from app.workers.telegram_reply_consumer import process_single_update


class FakeCaseRepository:
    def __init__(self, case=None) -> None:
        self.case = case
        self.lookup_calls = []
        self.staff_messages = []

    async def find_by_reply_message(self, telegram_chat_id, reply_to_message_id, message_thread_id=None):
        self.lookup_calls.append((telegram_chat_id, reply_to_message_id, message_thread_id))
        return self.case

    async def record_staff_reply_message(self, **kwargs):
        self.staff_messages.append(kwargs)


class FakeResultRepository:
    def __init__(self, inserted=True) -> None:
        self.inserted = inserted
        self.results = []

    async def insert_idempotent(self, result):
        self.results.append(result)
        return {"inserted": self.inserted, "duplicate": not self.inserted, "id": 501 if self.inserted else None}


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.calls = []

    async def process_result_transactionally(self, result, graph_state, outbound_messages, external_commands=None, summary_message=None):
        self.calls.append(
            {
                "result": result,
                "graph_state": graph_state,
                "outbound_messages": outbound_messages,
                "external_commands": external_commands or [],
                "summary_message": summary_message,
            }
        )
        return {"outbound_inserts": [{"inserted": True}], "external_command_inserts": []}


def make_case():
    return {
        "id": 42,
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 77,
        "external_command_id": 99,
        "intent": "deposit_missing",
        "active_workflow": "deposit_missing",
        "telegram_chat_id": "-1001",
        "telegram_message_thread_id": 12,
        "root_message_id": 300,
        "status": "created",
    }


def make_update(text="still checking order 12345678"):
    return {
        "update_id": 9001,
        "message": {
            "message_id": 301,
            "message_thread_id": 12,
            "chat": {"id": -1001},
            "from": {"id": 222, "username": "staff_a", "first_name": "Staff"},
            "reply_to_message": {"message_id": 300, "message_thread_id": 12},
            "text": text,
        },
    }


def test_telegram_reply_consumer_ignores_non_reply_message():
    update = {"update_id": 1, "message": {"message_id": 10, "chat": {"id": -1001}, "text": "hello"}}
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()

    result = asyncio.run(
        process_single_update(
            update,
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result == {"update_id": 1, "status": "IGNORED", "reason": "not_reply_to_case"}
    assert result_repository.results == []
    assert transaction_repository.calls == []


def test_telegram_reply_consumer_records_reply_and_writes_outbox():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()

    result = asyncio.run(
        process_single_update(
            make_update(),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
            bot_user_id=999,
        )
    )

    assert result["status"] == "RECORDED"
    assert result_repository.results[0]["result_type"] == "telegram.staff_reply.received"
    assert result_repository.results[0]["result_json"]["reply_to_message_id"] == 300
    assert transaction_repository.calls[0]["graph_state"]["workflow_stage"] == "waiting_backend"
    outbound = transaction_repository.calls[0]["outbound_messages"][0]
    assert "后台已收到" in outbound["payload_json"]["text"]
    assert "12345678" in outbound["payload_json"]["text"]
    assert case_repository.staff_messages[0]["telegram_message_id"] == 301


def test_telegram_reply_consumer_staff_reply_outbox_uses_independent_dedup():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()

    result = asyncio.run(
        process_single_update(
            make_update(),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "RECORDED"
    result_row = result_repository.results[0]
    outbound = transaction_repository.calls[0]["outbound_messages"][0]
    assert outbound["conversation_id"] == "livechat:chat-1"
    assert outbound["inbound_event_id"] == 77
    assert outbound["action_type"] == "send_event"
    assert outbound["dedup_key"] == f"{result_row['dedup_key']}:outbound"
    assert outbound["command_type"] == "telegram.staff_reply"
    assert outbound["message_kind"] == "telegram_staff_reply"


def test_telegram_reply_consumer_duplicate_result_does_not_write_outbox():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository(inserted=False)
    transaction_repository = FakeTransactionRepository()

    result = asyncio.run(
        process_single_update(
            make_update(),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "DUPLICATE"
    assert transaction_repository.calls == []
    assert case_repository.staff_messages == []


def test_telegram_reply_consumer_ignores_self_message():
    update = make_update()
    update["message"]["from"]["id"] = 999

    result = asyncio.run(
        process_single_update(
            update,
            case_repository=FakeCaseRepository(case=make_case()),
            result_repository=FakeResultRepository(),
            transaction_repository=FakeTransactionRepository(),
            target_chat_ids={"-1001"},
            bot_user_id=999,
        )
    )

    assert result == {"update_id": 9001, "status": "IGNORED", "reason": "self_message"}
