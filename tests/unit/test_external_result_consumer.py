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

    async def process_result_transactionally(self, result: dict, graph_state: dict, outbound_messages: list[dict], external_commands=None):
        self.calls.append((result, graph_state, outbound_messages, external_commands or []))
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
