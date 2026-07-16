import asyncio

from app.db.telegram_repositories import _reply_language_from_slot_memory
from app.workers.telegram_reply_consumer import process_single_update, process_telegram_updates


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


class FakeOffsetRepository:
    def __init__(self) -> None:
        self.saved = []

    async def save_offset(self, offset_key, update_id):
        self.saved.append((offset_key, update_id))


class FakeFinalReplyService:
    def __init__(self, text: str | None = None, *, raise_error: bool = False) -> None:
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


def assert_no_internal_backend_label(text: str) -> None:
    lowered = text.lower()
    assert "后台" not in text
    assert "後台" not in text
    assert "backend" not in lowered


def test_telegram_case_reply_language_prefers_last_reply_language():
    assert _reply_language_from_slot_memory({"last_user_language": "zh-Hans", "last_reply_language": "en"}) == "en"
    assert _reply_language_from_slot_memory({"conversation_language": "es"}) == "es"
    assert _reply_language_from_slot_memory({}) is None


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
        "reply_language": "zh-Hans",
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


def test_telegram_reply_consumer_does_not_advance_offset_or_process_later_updates_after_failure():
    class FailingCaseRepository(FakeCaseRepository):
        async def find_by_reply_message(self, telegram_chat_id, reply_to_message_id, message_thread_id=None):
            self.lookup_calls.append((telegram_chat_id, reply_to_message_id, message_thread_id))
            raise RuntimeError("temporary database outage")

    first = make_update()
    second = {**make_update(), "update_id": 9002, "message": {**make_update()["message"], "message_id": 302}}
    case_repository = FailingCaseRepository(case=make_case())
    offset_repository = FakeOffsetRepository()

    results = asyncio.run(
        process_telegram_updates(
            [first, second],
            case_repository=case_repository,
            result_repository=FakeResultRepository(),
            transaction_repository=FakeTransactionRepository(),
            offset_repository=offset_repository,
            offset_key="telegram:test",
            target_chat_ids={"-1001"},
        )
    )

    assert results == [{"update_id": 9001, "status": "FAILED", "error": "temporary database outage"}]
    assert len(case_repository.lookup_calls) == 1
    assert offset_repository.saved == []


def test_telegram_reply_consumer_advances_offset_for_ignored_update():
    offset_repository = FakeOffsetRepository()

    results = asyncio.run(
        process_telegram_updates(
            [{"update_id": 7}],
            case_repository=FakeCaseRepository(),
            result_repository=FakeResultRepository(),
            transaction_repository=FakeTransactionRepository(),
            offset_repository=offset_repository,
            offset_key="telegram:test",
        )
    )

    assert results == [{"update_id": 7, "status": "IGNORED", "reason": "missing_message"}]
    assert offset_repository.saved == [("telegram:test", 7)]


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
    assert "正在为您确认" in outbound["payload_json"]["text"]
    assert "12345678" in outbound["payload_json"]["text"]
    assert_no_internal_backend_label(outbound["payload_json"]["text"])
    assert case_repository.staff_messages[0]["telegram_message_id"] == 301


def test_telegram_reply_consumer_ignores_staff_reply_after_livechat_human_takeover():
    class HumanActiveTransactionRepository(FakeTransactionRepository):
        async def is_human_active(self, conversation_id: str) -> bool:
            assert conversation_id == "livechat:chat-1"
            return True

        async def process_result_transactionally(self, *args, **kwargs):
            raise AssertionError("Human-active Telegram reply must not create business work")

    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = HumanActiveTransactionRepository()

    result = asyncio.run(
        process_single_update(
            make_update(),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result == {
        "update_id": 9001,
        "status": "IGNORED",
        "reason": "conversation_human_active",
        "telegram_case_id": 42,
    }
    assert result_repository.results == []
    assert case_repository.staff_messages == []


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


def test_telegram_reply_consumer_uses_case_reply_language_for_staff_reply():
    case = {**make_case(), "reply_language": "en"}
    case_repository = FakeCaseRepository(case=case)
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()

    result = asyncio.run(
        process_single_update(
            make_update("Already forwarded to finance, once verified it will be credited to your account"),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "RECORDED"
    result_json = result_repository.results[0]["result_json"]
    assert result_json["language"] == "en"
    outbound = transaction_repository.calls[0]["outbound_messages"][0]
    assert outbound["payload_json"]["text"].startswith("The team has sent an update.")
    assert "关于您的存款问题" not in outbound["payload_json"]["text"]


def test_telegram_reply_consumer_passes_case_reply_language_to_final_reply():
    case = {**make_case(), "reply_language": "en"}
    case_repository = FakeCaseRepository(case=case)
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()
    final_reply_service = FakeFinalReplyService("The team has forwarded your case to finance. Please wait for the verification result.")

    result = asyncio.run(
        process_single_update(
            make_update("Already forwarded to finance, once verified it will be credited to your account"),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "RECORDED"
    assert final_reply_service.calls[0]["reply_language"] == "en"
    assert final_reply_service.calls[0]["conversation_language"] == "en"
    outbound = transaction_repository.calls[0]["outbound_messages"][0]
    assert outbound["payload_json"]["text"].startswith("The team has forwarded")


def test_telegram_reply_consumer_staff_reply_uses_final_reply_when_available():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()
    final_reply_service = FakeFinalReplyService("已为您核实到款项已到账，请刷新页面后确认账户余额。")

    result = asyncio.run(
        process_single_update(
            make_update("已经到账，刷新一下页面看看"),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "RECORDED"
    outbound = transaction_repository.calls[0]["outbound_messages"][0]
    assert outbound["payload_json"]["text"] == "已为您核实到款项已到账，请刷新页面后确认账户余额。"
    assert_no_internal_backend_label(outbound["payload_json"]["text"])
    assert "继续协助" in final_reply_service.calls[0]["response_text"]
    assert_no_internal_backend_label(final_reply_service.calls[0]["response_text"])
    assert final_reply_service.calls[0]["reply_plan"]["kind"] == "telegram_staff_reply"
    assert final_reply_service.calls[0]["reply_plan"]["allowed_facts"] == ["已经到账，刷新一下页面看看"]
    assert final_reply_service.calls[0]["node_reply_template"] == "telegram_staff_reply"
    assert final_reply_service.calls[0]["node_facts"]["staff_reply"] == "已经到账，刷新一下页面看看"
    assert final_reply_service.calls[0]["workflow_stage"] == "completed"


def test_telegram_reply_consumer_resolution_marks_case_completed_and_clears_active_tg_state():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()

    result = asyncio.run(
        process_single_update(
            make_update("As per checking, the deposit has been credited successfully."),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "RECORDED"
    graph_state = transaction_repository.calls[0]["graph_state"]
    assert graph_state["status"] == "AI_ACTIVE"
    assert graph_state["workflow_stage"] == "completed"
    assert graph_state["active_workflow"] is None
    assert graph_state["telegram_case_update"] == {
        "telegram_case_id": 42,
        "status": "completed_by_staff",
    }
    slot_memory = graph_state["slot_memory"]
    assert slot_memory["telegram_case_resolved_at"]
    assert slot_memory["telegram_case_resolution_text"] == "As per checking, the deposit has been credited successfully."
    assert slot_memory["customer_confirmed_resolved"] is False
    assert slot_memory["telegram_case_resolution_type"] == "resolution"
    assert slot_memory["telegram_case_resolution_workflow"] == "deposit_missing"
    for key in (
        "telegram_case_id",
        "telegram_message_id",
        "telegram_case_status",
        "telegram_append_status",
        "last_telegram_append_message_id",
        "telegram_staff_reply_status",
        "last_telegram_staff_reply_type",
        "last_telegram_staff_reply_message_id",
    ):
        assert key not in slot_memory


def test_telegram_reply_consumer_unknown_staff_text_does_not_complete_case():
    transaction_repository = FakeTransactionRepository()

    asyncio.run(
        process_single_update(
            make_update("noted"),
            case_repository=FakeCaseRepository(case=make_case()),
            result_repository=FakeResultRepository(),
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    graph_state = transaction_repository.calls[0]["graph_state"]
    assert graph_state["workflow_stage"] == "waiting_backend"
    assert graph_state["telegram_case_update"] == {
        "telegram_case_id": 42,
        "status": "under_review",
    }


def test_telegram_reply_consumer_opposite_completion_does_not_complete_case():
    transaction_repository = FakeTransactionRepository()

    asyncio.run(
        process_single_update(
            make_update("The withdrawal has been completed successfully."),
            case_repository=FakeCaseRepository(case=make_case()),
            result_repository=FakeResultRepository(),
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    graph_state = transaction_repository.calls[0]["graph_state"]
    assert graph_state["workflow_stage"] == "waiting_backend"
    assert graph_state["telegram_case_update"]["status"] == "under_review"


def test_telegram_reply_consumer_staff_reply_final_reply_failure_uses_fallback():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()
    final_reply_service = FakeFinalReplyService(raise_error=True)

    result = asyncio.run(
        process_single_update(
            make_update("已经到账，刷新一下页面看看"),
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "RECORDED"
    outbound = transaction_repository.calls[0]["outbound_messages"][0]
    assert "继续协助" in outbound["payload_json"]["text"]
    assert_no_internal_backend_label(outbound["payload_json"]["text"])


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


def test_telegram_reply_consumer_accepts_reply_to_recorded_attachment_message():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()
    update = make_update("checking attachment reply")
    update["message"]["reply_to_message"]["message_id"] = 301

    result = asyncio.run(
        process_single_update(
            update,
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "RECORDED"
    assert case_repository.lookup_calls == [("-1001", 301, 12)]
    assert result_repository.results[0]["result_json"]["reply_to_message_id"] == 301


def test_telegram_reply_consumer_forwards_largest_photo_without_marking_case_resolved():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()
    update = make_update("")
    update["message"].pop("text", None)
    update["message"]["photo"] = [{"file_id": "photo-small"}, {"file_id": "photo-large"}]

    result = asyncio.run(
        process_single_update(
            update,
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "RECORDED"
    assert result_repository.results[0]["result_json"]["attachment_file_ids"] == ["photo-large"]
    call = transaction_repository.calls[0]
    assert call["graph_state"]["status"] == "WAITING_EXTERNAL"
    assert call["graph_state"]["workflow_stage"] == "waiting_backend"
    assert call["graph_state"]["active_workflow"] == "deposit_missing"
    assert len(call["outbound_messages"]) == 1
    outbound = call["outbound_messages"][0]
    assert outbound["message_type"] == "image"
    assert outbound["command_type"] == "livechat.send_image"
    assert outbound["payload_json"] == {
        "asset_source": "telegram",
        "telegram_file_id": "photo-large",
        "caption": "",
    }


def test_telegram_reply_consumer_forwards_photo_with_processed_caption():
    case_repository = FakeCaseRepository(case=make_case())
    result_repository = FakeResultRepository()
    transaction_repository = FakeTransactionRepository()
    update = make_update("")
    update["message"].pop("text", None)
    update["message"]["caption"] = "here's the receipt"
    update["message"]["photo"] = [{"file_id": "photo-small"}, {"file_id": "photo-large"}]

    result = asyncio.run(
        process_single_update(
            update,
            case_repository=case_repository,
            result_repository=result_repository,
            transaction_repository=transaction_repository,
            target_chat_ids={"-1001"},
        )
    )

    assert result["status"] == "RECORDED"
    outbounds = transaction_repository.calls[0]["outbound_messages"]
    assert [outbound["message_type"] for outbound in outbounds] == ["image", "text"]
    assert [outbound["block_index"] for outbound in outbounds] == [0, 1]
    assert outbounds[0]["payload_json"] == {
        "asset_source": "telegram",
        "telegram_file_id": "photo-large",
        "caption": "",
    }
    assert outbounds[1]["payload_json"]["text"] == "已收到处理更新，我们会按照这个更新继续协助您处理。"
    assert outbounds[0]["dedup_key"].endswith(":outbound:image")
    assert outbounds[1]["dedup_key"].endswith(":outbound:caption")


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
