import asyncio


class FakeConversationRepository:
    def __init__(self) -> None:
        self.updated = []

    async def update_workflow_state(self, conversation_id: str, graph_state: dict) -> None:
        self.updated.append((conversation_id, graph_state))


class FakeOutboundRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert_idempotent(self, message: dict) -> dict:
        self.inserted.append(message)
        return {"inserted": True, "duplicate": False, "id": len(self.inserted)}


class DedupingFakeOutboundRepository:
    def __init__(self, existing_keys: set[str] | None = None) -> None:
        self.keys = set(existing_keys or set())
        self.inserted = []

    async def insert_idempotent(self, message: dict) -> dict:
        key = message.get("dedup_key") or (
            f"{message.get('tenant_id') or 'default'}:"
            f"{message.get('conversation_id') or ''}:"
            f"{message.get('inbound_event_id') or ''}:"
            f"{message['action_type']}"
        )
        if key in self.keys:
            return {"inserted": False, "duplicate": True, "id": None}
        self.keys.add(key)
        self.inserted.append(message)
        return {"inserted": True, "duplicate": False, "id": len(self.inserted)}


class FakeResultRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.processed = []
        self.failed = []
        self.leased = []

    async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
        self.leased.append((limit, worker_id, lease_seconds))
        return self.rows[:limit]

    async def mark_processed(self, result_id: int) -> None:
        self.processed.append(result_id)

    async def mark_failed(self, result_id: int, error: str) -> None:
        self.failed.append((result_id, error))

    async def mark_processing_failed(self, result_id: int, error: str, max_retries: int = 3) -> None:
        self.failed.append((result_id, error, max_retries))


class FakeTransactionRepository:
    def __init__(self, result_repository, conversation_repository, outbound_repository) -> None:
        self.result_repository = result_repository
        self.conversation_repository = conversation_repository
        self.outbound_repository = outbound_repository
        self.calls = []

    async def process_result_transactionally(
        self,
        result: dict,
        graph_state: dict,
        outbound_messages: list[dict],
        external_commands=None,
        summary_message=None,
    ):
        self.calls.append((result, graph_state, outbound_messages, external_commands or [], summary_message))
        await self.conversation_repository.update_workflow_state(result["conversation_id"], graph_state)
        outbound_inserts = []
        for message in outbound_messages:
            outbound_inserts.append(await self.outbound_repository.insert_idempotent(message))
        await self.result_repository.mark_processed(result["id"])
        return {"outbound_inserts": outbound_inserts, "external_command_inserts": []}


class FakeFinalReplyService:
    def __init__(self, text: str | None = None, *, raise_error: bool = False, fallback: bool = False) -> None:
        self.text = text
        self.raise_error = raise_error
        self.fallback = fallback
        self.calls = []

    async def compose(self, state: dict) -> dict:
        self.calls.append(state)
        if self.raise_error:
            raise RuntimeError("final reply failed")
        if self.fallback:
            return {
                **state,
                "final_response_text": state["response_text_fallback"],
                "final_reply_result": {"status": "fallback", "fallback_reason": "low_confidence"},
            }
        return {
            **state,
            "final_response_text": self.text or state["response_text_fallback"],
            "final_reply_result": {"status": "accepted", "confidence": 0.91},
        }


class FakeConversationMessageRepository:
    def __init__(self, recent_messages: list[dict]) -> None:
        self.recent_messages = recent_messages
        self.calls = []

    async def fetch_recent(self, conversation_id: str, limit: int = 10):
        self.calls.append((conversation_id, limit))
        return list(self.recent_messages)


def make_result(result_type: str, command_type: str | None = None) -> dict:
    command_type = command_type or result_type.removesuffix(".mock_result")
    return {
        "id": 7,
        "external_command_id": 99,
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 11,
        "command_type": command_type,
        "result_type": result_type,
        "result_json": {"status": "MOCKED"},
    }


def run_consumer_for(result: dict):
    from app.workers.external_result_consumer import process_pending_results

    result_repository = FakeResultRepository([result])
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    transaction_repository = FakeTransactionRepository(result_repository, conversation_repository, outbound_repository)

    processed = asyncio.run(
        process_pending_results(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=transaction_repository,
            limit=20,
            worker_id="consumer-a",
        )
    )
    return processed, result_repository, conversation_repository, outbound_repository


def test_external_result_consumer_cli_accepts_lease_options():
    from app.workers.external_result_consumer import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--once", "--limit", "20", "--worker-id", "local-dev-1", "--lease-seconds", "60", "--max-retries", "5"]
    )

    assert args.worker_id == "local-dev-1"
    assert args.lease_seconds == 60
    assert args.max_retries == 5
    assert args.recover_interval_seconds == 30


def test_external_result_consumer_cli_accepts_recover_interval():
    from app.workers.external_result_consumer import build_arg_parser

    args = build_arg_parser().parse_args(["--recover-interval-seconds", "0"])

    assert args.recover_interval_seconds == 0


def test_external_result_consumer_recovery_disabled_does_not_call_repository():
    from app.workers.external_result_consumer import maybe_recover_expired_leases

    class FakeRepository:
        def __init__(self) -> None:
            self.calls = 0

        async def recover_expired_leases(self):
            self.calls += 1

    repository = FakeRepository()

    result = asyncio.run(
        maybe_recover_expired_leases(
            repository,
            last_recovered_at=None,
            recover_interval_seconds=0,
            now=100.0,
        )
    )

    assert result is None
    assert repository.calls == 0


def test_external_result_consumer_recovery_runs_when_interval_elapsed():
    from app.workers.external_result_consumer import maybe_recover_expired_leases

    class FakeRepository:
        def __init__(self) -> None:
            self.calls = 0

        async def recover_expired_leases(self):
            self.calls += 1
            return 2

    repository = FakeRepository()

    unchanged = asyncio.run(
        maybe_recover_expired_leases(
            repository,
            last_recovered_at=95.0,
            recover_interval_seconds=30,
            now=100.0,
        )
    )
    updated = asyncio.run(
        maybe_recover_expired_leases(
            repository,
            last_recovered_at=60.0,
            recover_interval_seconds=30,
            now=100.0,
        )
    )

    assert unchanged == 95.0
    assert updated == 100.0
    assert repository.calls == 1


def test_external_result_consumer_recovery_failure_is_logged_and_does_not_raise(caplog):
    from app.workers.external_result_consumer import maybe_recover_expired_leases

    class FakeRepository:
        async def recover_expired_leases(self):
            raise RuntimeError("recovery failed")

    result = asyncio.run(
        maybe_recover_expired_leases(
            FakeRepository(),
            last_recovered_at=None,
            recover_interval_seconds=30,
            now=100.0,
        )
    )

    assert result == 100.0
    assert "Failed to recover expired external_command_result leases." in caplog.text


def test_result_consumer_case_card_generates_waiting_reply_before_processed():
    processed, result_repository, conversation_repository, outbound_repository = run_consumer_for(
        make_result("telegram.send_case_card.mock_result")
    )

    assert "资料已收到" in outbound_repository.inserted[0]["payload_json"]["text"]
    assert conversation_repository.updated[0][1]["workflow_stage"] == "waiting_backend"
    assert result_repository.leased == [(20, "consumer-a", 60)]
    assert result_repository.processed == [7]
    assert processed[0]["status"] == "PROCESSED"


def test_external_result_consumer_process_result_by_id_leases_only_requested_result():
    from app.workers.external_result_consumer import process_result_by_id

    row = make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "scoped ok"}}

    class ScopedResultRepository(FakeResultRepository):
        async def lease_pending_by_id(self, result_id: int, worker_id: str, lease_seconds: int):
            self.leased.append((result_id, worker_id, lease_seconds))
            assert result_id == 700
            return row

    result_repository = ScopedResultRepository([])
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()

    result = asyncio.run(
        process_result_by_id(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            result_id=700,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            worker_id="consumer-scoped",
        )
    )

    assert result_repository.leased == [(700, "consumer-scoped", 60)]
    assert result["status"] == "PROCESSED"
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "scoped ok"


def test_backend_query_result_outbound_uses_backend_answer_dedup_after_instant_reply():
    from app.workers.external_result_consumer import process_result_by_id

    row = make_result("backend.query.result") | {
        "id": 700,
        "result_json": {"status": "success", "answer": "backend answer ok"},
    }

    class ScopedResultRepository(FakeResultRepository):
        async def lease_pending_by_id(self, result_id: int, worker_id: str, lease_seconds: int):
            return row

    result_repository = ScopedResultRepository([])
    conversation_repository = FakeConversationRepository()
    outbound_repository = DedupingFakeOutboundRepository(
        existing_keys={"default:livechat:chat-1:11:send_event"}
    )

    final_reply_service = FakeFinalReplyService("您好，后台显示还需要完成 1375.09 流水后再提款。")

    result = asyncio.run(
        process_result_by_id(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            result_id=700,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            worker_id="consumer-scoped",
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert result["status"] == "PROCESSED"
    assert final_reply_service.calls
    assert final_reply_service.calls[0]["reply_plan"]["kind"] == "backend_query_result"
    assert result["outbound_inserts"][0]["inserted"] is True
    outbound = outbound_repository.inserted[0]
    assert outbound["dedup_key"] == "default:livechat:chat-1:11:backend.query.result:700"
    assert outbound["message_kind"] == "backend_answer"
    assert outbound["command_type"] == "backend.query.result"
    assert outbound["payload_json"]["text"] == "您好，后台显示还需要完成 1375.09 流水后再提款。"


def test_backend_query_result_renders_structured_spanish_player_not_found_without_answer():
    from app.workers.external_result_consumer import process_pending_results

    result_repository = FakeResultRepository(
        [
            make_result("backend.query.result")
            | {
                "result_json": {
                    "status": "success",
                    "reply_intent": "backend_player_not_found",
                    "reply_facts": {},
                    "reply_language": "es",
                    "query": {"player_found": False},
                }
            }
        ]
    )
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    transaction_repository = FakeTransactionRepository(result_repository, conversation_repository, outbound_repository)

    processed = asyncio.run(
        process_pending_results(
            result_repository,
            conversation_repository,
            outbound_repository,
            transaction_repository=transaction_repository,
            llm_final_reply_enabled=False,
        )
    )

    assert processed[0]["status"] == "PROCESSED"
    assert "No encontramos datos de jugador" in outbound_repository.inserted[0]["payload_json"]["text"]
    assert outbound_repository.inserted[0]["command_type"] == "backend.query.result"


def test_external_result_consumer_process_result_by_id_reports_locked():
    from app.workers.external_result_consumer import process_result_by_id

    class ScopedResultRepository(FakeResultRepository):
        async def lease_pending_by_id(self, result_id: int, worker_id: str, lease_seconds: int):
            return None

    result = asyncio.run(
        process_result_by_id(
            result_repository=ScopedResultRepository([]),
            conversation_repository=FakeConversationRepository(),
            outbound_repository=FakeOutboundRepository(),
            result_id=701,
            worker_id="consumer-scoped",
        )
    )

    assert result == {"id": 701, "status": "RESULT_LOCKED_OR_NOT_PENDING"}


def test_result_consumer_append_to_case_generates_supplement_reply():
    _processed, _result_repository, _conversation_repository, outbound_repository = run_consumer_for(
        make_result("telegram.append_to_case.mock_result")
    )

    assert "补充资料已收到" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_result_consumer_backend_query_does_not_fabricate_backend_facts():
    _processed, _result_repository, _conversation_repository, outbound_repository = run_consumer_for(
        make_result("backend.query.mock_result")
    )

    text = outbound_repository.inserted[0]["payload_json"]["text"]
    assert "dry-run" in text
    assert "未连接真实后台" in text


def test_result_consumer_pending_reply_does_not_fabricate_lookup_result():
    _processed, _result_repository, _conversation_repository, outbound_repository = run_consumer_for(
        make_result("pending_reply.lookup.mock_result")
    )

    text = outbound_repository.inserted[0]["payload_json"]["text"]
    assert "dry-run" in text
    assert "未连接真实 pending reply 查询源" in text


def test_result_consumer_handoff_updates_active_workflow():
    _processed, _result_repository, conversation_repository, outbound_repository = run_consumer_for(
        make_result("human_handoff.requested.mock_result")
    )

    assert conversation_repository.updated[0][1]["active_workflow"] == "human_handoff"
    assert conversation_repository.updated[0][1]["workflow_stage"] == "handoff_requested"
    assert "转接真人客服" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_result_consumer_rag_placeholder_does_not_fabricate_facts():
    _processed, _result_repository, _conversation_repository, outbound_repository = run_consumer_for(
        make_result("rag.placeholder.mock_result")
    )

    text = outbound_repository.inserted[0]["payload_json"]["text"]
    assert "RAG placeholder" in text
    assert "尚未接入真实知识库" in text


def test_result_consumer_telegram_case_created_updates_case_id():
    processed, result_repository, conversation_repository, outbound_repository = run_consumer_for(
        make_result("telegram.case.created")
        | {
            "result_json": {
                "case_id": "mock_case_001",
                "status": "created",
                "active_workflow": "deposit_missing",
                "telegram_message_id": 12345,
                "target_chat_id": "-100test",
            }
        }
    )

    assert processed[0]["status"] == "PROCESSED"
    assert result_repository.processed == [7]
    assert conversation_repository.updated[0][1]["workflow_stage"] == "waiting_backend"
    assert conversation_repository.updated[0][1]["active_workflow"] == "deposit_missing"
    assert conversation_repository.updated[0][1]["slot_memory"]["telegram_case_id"] == "mock_case_001"
    assert conversation_repository.updated[0][1]["slot_memory"]["telegram_message_id"] == 12345
    assert "案件已建立" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_result_consumer_builds_telegram_external_summary_message():
    from app.workers.external_result_consumer import build_result_handler

    row = make_result("telegram.case.created") | {"result_json": {"case_id": "mock_case_001", "status": "created"}}

    handler = build_result_handler(row)
    summary = handler["summary_message"]

    assert summary["sender_role"] == "telegram"
    assert summary["message_type"] == "external_result"
    assert summary["external_command_result_id"] == 7
    assert "case_id=mock_case_001" in summary["text_content"]


def test_result_consumer_builds_backend_external_summary_message():
    from app.workers.external_result_consumer import build_result_handler

    row = make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "查询已完成"}}

    handler = build_result_handler(row)
    summary = handler["summary_message"]

    assert summary["sender_role"] == "backend"
    assert summary["message_type"] == "external_result"
    assert summary["external_command_result_id"] == 7
    assert "后台查询成功" in summary["text_content"]


def test_result_consumer_telegram_case_created_without_case_id_fails():
    from app.workers.external_result_consumer import process_pending_results

    result_repository = FakeResultRepository([make_result("telegram.case.created") | {"result_json": {"status": "created"}}])

    processed = asyncio.run(
        process_pending_results(
            result_repository=result_repository,
            conversation_repository=FakeConversationRepository(),
            outbound_repository=FakeOutboundRepository(),
            transaction_repository=FakeTransactionRepository(result_repository, FakeConversationRepository(), FakeOutboundRepository()),
            limit=20,
            worker_id="consumer-a",
            max_retries=2,
        )
    )

    assert result_repository.processed == []
    assert result_repository.failed == [(7, "telegram.case.created result missing case_id", 2)]
    assert processed[0]["status"] == "FAILED"


def test_result_consumer_pending_reply_lookup_result_found_writes_reply():
    _processed, result_repository, conversation_repository, outbound_repository = run_consumer_for(
        make_result("pending_reply.lookup.result")
        | {"result_json": {"status": "found", "reply_text": "上一笔案件仍在处理中，请稍候。"}}
    )

    assert result_repository.processed == [7]
    assert conversation_repository.updated[0][1]["workflow_stage"] == "pending_reply_found"
    assert "上一笔案件仍在处理中" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_result_consumer_pending_reply_lookup_result_not_found_writes_safe_reply():
    _processed, result_repository, conversation_repository, outbound_repository = run_consumer_for(
        make_result("pending_reply.lookup.result")
        | {"result_json": {"status": "not_found", "reply_text": "目前没有找到这组资料的上一笔有效案件。"}}
    )

    assert result_repository.processed == [7]
    assert conversation_repository.updated[0][1]["workflow_stage"] == "pending_reply_not_found"
    assert conversation_repository.updated[0][1]["active_workflow"] is None
    assert "没有找到" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_result_consumer_backend_query_result_success_uses_final_reply_text():
    from app.workers.external_result_consumer import process_pending_results

    final_reply_service = FakeFinalReplyService("润色后的后台回复。")
    result_repository = FakeResultRepository(
        [
            make_result("backend.query.result")
            | {
                "result_json": {
                    "status": "success",
                    "answer": "查询已完成，当前为 mock 后台结果。",
                    "query": {"remaining_turnover": 1375.09, "active_requirements_count": 2},
                    "reply_language": "zh-Hans",
                }
            }
        ]
    )
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    transaction_repository = FakeTransactionRepository(result_repository, conversation_repository, outbound_repository)
    message_repository = FakeConversationMessageRepository(
        [
            {"sender_role": "customer", "text_content": "我无法提款"},
            {"sender_role": "assistant", "text_content": "剩余流水约为 1375.09。"},
            {"sender_role": "customer", "text_content": "刚刚是说我还有多少流水？"},
        ]
    )

    processed = asyncio.run(
        process_pending_results(
            result_repository,
            conversation_repository,
            outbound_repository,
            transaction_repository=transaction_repository,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
            conversation_message_repository=message_repository,
        )
    )

    assert processed[0]["status"] == "PROCESSED"
    assert result_repository.processed == [7]
    assert final_reply_service.calls
    assert final_reply_service.calls[0]["reply_language"] == "zh-Hans"
    assert final_reply_service.calls[0]["recent_messages"] == message_repository.recent_messages
    assert final_reply_service.calls[0]["node_reply_template"] == "backend_result"
    assert final_reply_service.calls[0]["node_facts"]["query"]["remaining_turnover"] == 1375.09
    assert final_reply_service.calls[0]["backend_result"]["answer"] == "查询已完成，当前为 mock 后台结果。"
    assert final_reply_service.calls[0]["backend_result"]["query"]["remaining_turnover"] == 1375.09
    assert "1375.09" in final_reply_service.calls[0]["reply_plan"]["must_say_exact"]
    assert conversation_repository.updated[0][1]["workflow_stage"] == "completed"
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "润色后的后台回复。"
    assert outbound_repository.inserted[0]["dedup_key"] == "default:livechat:chat-1:11:backend.query.result:7"
    assert outbound_repository.inserted[0]["message_kind"] == "backend_answer"
    assert outbound_repository.inserted[0]["command_type"] == "backend.query.result"


def test_result_consumer_backend_query_result_final_reply_fallback_uses_internal_answer():
    from app.workers.external_result_consumer import process_pending_results

    final_reply_service = FakeFinalReplyService(fallback=True)
    result_repository = FakeResultRepository(
        [make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "内部安全 answer"}}]
    )
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    transaction_repository = FakeTransactionRepository(result_repository, conversation_repository, outbound_repository)

    asyncio.run(
        process_pending_results(
            result_repository,
            conversation_repository,
            outbound_repository,
            transaction_repository=transaction_repository,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert final_reply_service.calls
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "内部安全 answer"


def test_result_consumer_backend_query_result_final_reply_exception_uses_internal_answer():
    from app.workers.external_result_consumer import process_pending_results

    final_reply_service = FakeFinalReplyService(raise_error=True)
    result_repository = FakeResultRepository(
        [make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "内部安全 answer"}}]
    )
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    transaction_repository = FakeTransactionRepository(result_repository, conversation_repository, outbound_repository)

    asyncio.run(
        process_pending_results(
            result_repository,
            conversation_repository,
            outbound_repository,
            transaction_repository=transaction_repository,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert final_reply_service.calls
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "内部安全 answer"


def test_result_consumer_backend_query_result_failed_writes_safe_fallback_without_retry():
    from app.workers.external_result_consumer import process_pending_results

    final_reply_service = FakeFinalReplyService("后台暂时查不到结果，我们会继续人工复核。")
    result = make_result("backend.query.result") | {
        "result_json": {
            "status": "failed",
            "error_code": "FAILED_CONFIG",
            "error_message": "Authorization Bearer secret-token password=secret",
        }
    }
    result_repository = FakeResultRepository([result])
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()
    transaction_repository = FakeTransactionRepository(result_repository, conversation_repository, outbound_repository)

    processed = asyncio.run(
        process_pending_results(
            result_repository,
            conversation_repository,
            outbound_repository,
            transaction_repository=transaction_repository,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=True,
        )
    )

    assert processed[0]["status"] == "PROCESSED"
    assert result_repository.processed == [7]
    assert result_repository.failed == []
    assert final_reply_service.calls
    assert outbound_repository.inserted[0]["payload_json"]["text"] == "后台暂时查不到结果，我们会继续人工复核。"
    assert outbound_repository.inserted[0]["dedup_key"] == "default:livechat:chat-1:11:backend.query.result:7"
    assert outbound_repository.inserted[0]["message_kind"] == "backend_answer"
    assert outbound_repository.inserted[0]["command_type"] == "backend.query.result"
    graph_state = conversation_repository.updated[0][1]
    assert graph_state["status"] == "WAITING_EXTERNAL"
    assert graph_state["active_workflow"] == "withdrawal_blocked_or_rollover"
    assert graph_state["workflow_stage"] == "backend_query_failed_waiting_manual"
    assert graph_state["slot_memory"]["backend_query_status"] == "failed"
    assert graph_state["slot_memory"]["backend_query_error_code"] == "FAILED_CONFIG"
    assert "secret-token" not in str(outbound_repository.inserted)


def test_result_consumer_marks_failed_when_outbound_write_fails():
    from app.workers.external_result_consumer import process_pending_results

    class FailingOutboundRepository:
        async def insert_idempotent(self, message: dict) -> dict:
            raise RuntimeError("outbox failed")

    result_repository = FakeResultRepository([make_result("backend.query.mock_result")])
    conversation_repository = FakeConversationRepository()
    outbound_repository = FailingOutboundRepository()

    processed = asyncio.run(
        process_pending_results(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            limit=20,
            worker_id="consumer-a",
            max_retries=2,
        )
    )

    assert result_repository.processed == []
    assert result_repository.failed == [(7, "outbox failed", 2)]
    assert processed[0]["status"] == "FAILED"


def test_external_result_consumer_two_worker_leases_do_not_overlap():
    from app.workers.external_result_consumer import process_pending_results

    class InMemoryResultRepository(FakeResultRepository):
        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.leased.append((limit, worker_id, lease_seconds))
            available = [row for row in self.rows if row.get("locked_by") is None][:limit]
            for row in available:
                row["locked_by"] = worker_id
            return available

    repository = InMemoryResultRepository([make_result("backend.query.mock_result")])
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()

    first = asyncio.run(
        process_pending_results(
            result_repository=repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(repository, conversation_repository, outbound_repository),
            worker_id="consumer-a",
        )
    )
    second = asyncio.run(
        process_pending_results(
            result_repository=repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(repository, conversation_repository, outbound_repository),
            worker_id="consumer-b",
        )
    )

    assert [item["id"] for item in first] == [7]
    assert second == []


def test_external_result_consumer_polling_loop_sleeps_then_processes_second_round():
    from app.workers.external_result_consumer import run_polling_loop

    class DelayedResultRepository(FakeResultRepository):
        def __init__(self) -> None:
            super().__init__([])
            self.round = 0
            self.recovered = 0

        async def recover_expired_leases(self):
            self.recovered += 1
            return 0

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.round += 1
            if self.round == 1:
                return []
            return [make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "mock ok"}}]

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    result_repository = DelayedResultRepository()
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()

    asyncio.run(
        run_polling_loop(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            poll_seconds=5,
            limit=20,
            worker_id="consumer-a",
            recover_interval_seconds=0,
            iterations=2,
            sleep=fake_sleep,
        )
    )

    assert sleeps == [5]
    assert result_repository.processed == [7]
    assert "mock ok" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_external_result_consumer_polling_loop_recovery_failure_continues(caplog):
    from app.workers.external_result_consumer import run_polling_loop

    class RecoveringResultRepository(FakeResultRepository):
        def __init__(self) -> None:
            super().__init__([])
            self.recovery_calls = 0
            self.lease_calls = 0

        async def recover_expired_leases(self):
            self.recovery_calls += 1
            if self.recovery_calls == 1:
                raise RuntimeError("recover failed")
            return 0

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.lease_calls += 1
            if self.lease_calls == 2:
                return [make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "mock ok"}}]
            return []

    async def fake_sleep(seconds):
        return None

    result_repository = RecoveringResultRepository()
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()

    asyncio.run(
        run_polling_loop(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            poll_seconds=5,
            limit=20,
            worker_id="consumer-a",
            recover_interval_seconds=1,
            last_recovered_at=-10.0,
            iterations=2,
            sleep=fake_sleep,
        )
    )

    assert "Failed to recover expired external_command_result leases." in caplog.text
    assert result_repository.processed == [7]


def test_external_result_consumer_crash_recovery_processes_expired_result_once():
    from app.workers.external_result_consumer import process_pending_results

    class InMemoryResultRepository(FakeResultRepository):
        def __init__(self) -> None:
            super().__init__([make_result("backend.query.result") | {"result_json": {"status": "success", "answer": "mock ok"}}])
            self.rows[0]["locked_by"] = None
            self.rows[0]["expired"] = False

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            row = self.rows[0]
            if row.get("status") == "PROCESSED":
                return []
            if row.get("locked_by") is None or row.get("expired"):
                row["locked_by"] = worker_id
                row["expired"] = False
                return [row]
            return []

        async def recover_expired_leases(self):
            row = self.rows[0]
            if row.get("locked_by") and row.get("expired"):
                row["locked_by"] = None
                return 1
            return 0

        async def mark_processed(self, result_id: int) -> None:
            await super().mark_processed(result_id)
            self.rows[0]["status"] = "PROCESSED"

    result_repository = InMemoryResultRepository()
    conversation_repository = FakeConversationRepository()
    outbound_repository = FakeOutboundRepository()

    first = asyncio.run(result_repository.lease_pending(limit=1, worker_id="consumer-a", lease_seconds=1))
    result_repository.rows[0]["expired"] = True
    recovered = asyncio.run(result_repository.recover_expired_leases())
    second = asyncio.run(
        process_pending_results(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            worker_id="consumer-b",
        )
    )
    third = asyncio.run(
        process_pending_results(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=FakeTransactionRepository(result_repository, conversation_repository, outbound_repository),
            worker_id="consumer-c",
        )
    )

    assert [row["id"] for row in first] == [7]
    assert recovered == 1
    assert [item["id"] for item in second] == [7]
    assert third == []
    assert result_repository.processed == [7]
