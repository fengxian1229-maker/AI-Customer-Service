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
        for message in outbound_messages:
            await self.outbound_repository.insert_idempotent(message)
        await self.result_repository.mark_processed(result["id"])
        return {"outbound_inserts": [{"inserted": True}], "external_command_inserts": []}


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


def test_result_consumer_backend_query_result_success_uses_result_answer():
    _processed, result_repository, conversation_repository, outbound_repository = run_consumer_for(
        make_result("backend.query.result")
        | {"result_json": {"status": "success", "answer": "查询已完成，当前为 mock 后台结果。"}}
    )

    assert result_repository.processed == [7]
    assert conversation_repository.updated[0][1]["workflow_stage"] == "completed"
    assert "mock 后台结果" in outbound_repository.inserted[0]["payload_json"]["text"]


def test_result_consumer_backend_query_result_failed_is_retryable_failure():
    from app.workers.external_result_consumer import process_pending_results

    result_repository = FakeResultRepository(
        [
            make_result("backend.query.result")
            | {"result_json": {"status": "failed", "error_code": "NOT_FOUND", "error_message": "找不到订单"}}
        ]
    )

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
    assert result_repository.failed == [(7, "backend.query.result failed: NOT_FOUND 找不到订单", 2)]
    assert processed[0]["status"] == "FAILED"


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
