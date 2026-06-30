import asyncio


def test_runner_plan_only_does_not_execute_backend_or_send(monkeypatch):
    from app.workers import withdrawal_backend_smoke_runner as runner

    calls = []

    class FakeRepository:
        async def get_inbound(self, inbound_event_id):
            return {
                "id": inbound_event_id,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "event_id": "event-1",
                "standard_event_type": "MESSAGE_CREATED",
                "processed": 1,
                "ignored": 0,
                "payload_json": {"event": {"text": "提款不了，用户名是 user-a"}},
            }

        async def list_backend_commands(self, inbound_event_id):
            return [
                {
                    "id": 10,
                    "command_type": "backend.query",
                    "status": "PENDING",
                    "payload_json": {"intent": "withdrawal_blocked_or_rollover"},
                }
            ]

        async def list_results_for_command(self, command_id):
            return []

    async def fail_gateway(*args, **kwargs):
        raise AssertionError("plan-only must not process gateway")

    async def fail_sender(*args, **kwargs):
        raise AssertionError("plan-only must not send")

    monkeypatch.setattr(runner.gateway_consumer, "process_inbound_event_id", fail_gateway)
    monkeypatch.setattr(runner.sender_worker, "process_pending_for_inbound_event", fail_sender)

    result = asyncio.run(
        runner.run_smoke(
            pool=object(),
            repository=FakeRepository(),
            inbound_event_id=123,
            plan_only=True,
        )
    )

    assert calls == []
    assert result["mode"] == "plan_only"
    assert result["smoke_status"] == "PLAN_BACKEND_EXECUTION"
    assert result["execute_backend"] is False
    assert result["send_livechat"] is False
    assert result["steps"]["backend_command"]["command_id"] == 10
    assert result["steps"]["backend_execute"]["changed_db"] is False
    assert result["exit_code"] == 0
    assert result["terminal_status"] == "PLAN_READY"


def test_runner_inbound_not_found_returns_no_inbound():
    from app.workers import withdrawal_backend_smoke_runner as runner

    class FakeRepository:
        async def get_inbound(self, inbound_event_id):
            return None

    result = asyncio.run(
        runner.run_smoke(
            pool=object(),
            repository=FakeRepository(),
            inbound_event_id=404,
        )
    )

    assert result["smoke_status"] == "NO_INBOUND"
    assert result["closed_loop"] is False
    assert result["exit_code"] == 1
    assert result["terminal_status"] == "FAILED"


def test_runner_execute_mode_uses_scoped_gateway_sender_command_and_result(monkeypatch):
    from app.workers import withdrawal_backend_smoke_runner as runner

    calls = []

    class FakeRepository:
        async def get_inbound(self, inbound_event_id):
            return {
                "id": inbound_event_id,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "event_id": "event-1",
                "standard_event_type": "MESSAGE_CREATED",
                "processed": 0,
                "ignored": 0,
                "payload_json": {"event": {"text": "提款不了，用户名是 user-a"}},
            }

        async def list_backend_commands(self, inbound_event_id):
            return [
                {"id": 10, "command_type": "backend.query", "status": "PENDING", "payload_json": {}},
                {"id": 11, "command_type": "backend.query", "status": "PENDING", "payload_json": {}},
            ]

        async def list_results_for_command(self, command_id):
            return [{"id": 99, "status": "PENDING", "result_json": {"status": "success", "answer": "ok"}}]

    class FakeCommandRepository:
        async def lease_pending_by_id(self, command_id, worker_id, lease_seconds):
            calls.append(("lease_command", command_id, worker_id, lease_seconds))
            return {
                "id": command_id,
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "inbound_event_id": 123,
                "command_type": "backend.query",
                "payload_json": {"intent": "withdrawal_blocked_or_rollover", "account_or_phone": "user-a"},
            }

        async def mark_sent(self, command_id):
            calls.append(("mark_sent", command_id))

    class FakeResultRepository:
        async def insert_idempotent(self, result):
            calls.append(("insert_result", result["external_command_id"]))
            return {"inserted": True, "duplicate": False, "id": 99}

        async def lease_pending_by_id(self, result_id, worker_id, lease_seconds):
            calls.append(("lease_result", result_id, worker_id, lease_seconds))
            return {
                "id": result_id,
                "external_command_id": 10,
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "inbound_event_id": 123,
                "command_type": "backend.query",
                "result_type": "backend.query.result",
                "result_json": {"status": "success", "answer": "ok"},
            }

    class FakeConversationRepository:
        pass

    class FakeOutboundRepository:
        pass

    class FakeAdminRepository:
        def __init__(self, pool):
            self.pool = pool

        async def by_inbound(self, inbound_event_id):
            return {
                "smoke_status": "BACKEND_ANSWER_SENT",
                "external_commands": [{"id": 10, "command_type": "backend.query", "status": "SENT"}],
                "external_command_results": [{"id": 99, "result_type": "backend.query.result", "status": "PROCESSED"}],
                "outbound_messages": [{"status": "SENT"}],
            }

    async def fake_gateway(pool, inbound_event_id, checkpoint_mode="off", settings=None):
        calls.append(("gateway", inbound_event_id))
        return {"processed": 1, "failed": 0, "not_found": False}

    async def fake_sender(pool, sender_client, inbound_event_id, limit=20):
        calls.append(("sender", inbound_event_id, type(sender_client).__name__))
        return [{"status": "SENT"}]

    async def fake_process_result_by_id(
        result_repository,
        conversation_repository,
        outbound_repository,
        result_id,
        transaction_repository=None,
        worker_id=None,
        lease_seconds=60,
        max_retries=3,
    ):
        calls.append(("consume_result", result_id, worker_id, lease_seconds))
        return {"id": result_id, "result_type": "backend.query.result", "status": "PROCESSED"}

    class FakeService:
        def execute(self, payload, tenant_id=None, channel_type=None, channel_instance_id=None):
            calls.append(("backend_execute", payload["account_or_phone"], tenant_id, channel_instance_id))
            return {"status": "success", "answer": "ok"}

    monkeypatch.setattr(runner.gateway_consumer, "process_inbound_event_id", fake_gateway)
    monkeypatch.setattr(runner.sender_worker, "process_pending_for_inbound_event", fake_sender)
    monkeypatch.setattr(runner, "ExternalCommandRepository", lambda pool: FakeCommandRepository())
    monkeypatch.setattr(runner, "ExternalCommandResultRepository", lambda pool: FakeResultRepository())
    monkeypatch.setattr(runner, "ConversationRepository", lambda pool: FakeConversationRepository())
    monkeypatch.setattr(runner, "OutboundMessageRepository", lambda pool: FakeOutboundRepository())
    monkeypatch.setattr(runner.external_command_worker, "_build_backend_query_service", lambda settings: FakeService())
    monkeypatch.setattr(runner.external_result_consumer, "process_result_by_id", fake_process_result_by_id)
    monkeypatch.setattr(runner.backend_sop_smoke_admin, "BackendSopSmokeReadRepository", FakeAdminRepository)

    result = asyncio.run(
        runner.run_smoke(
            pool=object(),
            repository=FakeRepository(),
            inbound_event_id=123,
            execute_backend=True,
            send_livechat=True,
        )
    )

    assert ("gateway", 123) in calls
    assert calls.count(("sender", 123, "LiveChatSenderClient")) == 2
    assert ("lease_command", 10, "withdrawal-smoke-runner", 60) in calls
    assert ("backend_execute", "user-a", "default", "chat-1") in calls
    assert ("consume_result", 99, "withdrawal-smoke-runner", 60) in calls
    assert result["steps"]["backend_command"]["duplicates_count"] == 1
    assert result["steps"]["backend_result_consume"]["status"] == "PROCESSED"


def test_runner_resumes_sent_command_with_pending_result_without_reexecuting_backend(monkeypatch):
    from app.workers import withdrawal_backend_smoke_runner as runner

    calls = []

    class FakeRepository:
        async def get_inbound(self, inbound_event_id):
            return {
                "id": inbound_event_id,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "event_id": "event-1",
                "standard_event_type": "MESSAGE_CREATED",
                "processed": 1,
                "ignored": 0,
                "payload_json": {"event": {"text": "提款不了，用户名是 user-a"}},
            }

        async def list_backend_commands(self, inbound_event_id):
            return [{"id": 10, "command_type": "backend.query", "status": "SENT", "payload_json": {}}]

        async def list_results_for_command(self, command_id):
            return [{"id": 99, "status": "PENDING", "result_json": {"status": "success", "answer": "ok"}}]

    class FakeCommandRepository:
        async def lease_pending_by_id(self, *args, **kwargs):
            raise AssertionError("SENT command must not be leased again")

    class FakeResultRepository:
        pass

    async def fake_process_result_by_id(**kwargs):
        calls.append(("consume_result", kwargs["result_id"], kwargs["worker_id"]))
        return {"id": kwargs["result_id"], "result_type": "backend.query.result", "status": "PROCESSED"}

    async def fake_sender(pool, sender_client, inbound_event_id, limit=20):
        calls.append(("sender", inbound_event_id))
        return []

    monkeypatch.setattr(runner, "ExternalCommandRepository", lambda pool: FakeCommandRepository())
    monkeypatch.setattr(runner, "ExternalCommandResultRepository", lambda pool: FakeResultRepository())
    monkeypatch.setattr(runner.external_result_consumer, "process_result_by_id", fake_process_result_by_id)
    monkeypatch.setattr(runner.sender_worker, "process_pending_for_inbound_event", fake_sender)

    result = asyncio.run(
        runner.run_smoke(
            pool=object(),
            repository=FakeRepository(),
            inbound_event_id=123,
            execute_backend=True,
            send_livechat=True,
        )
    )

    assert ("consume_result", 99, "withdrawal-smoke-runner") in calls
    assert ("sender", 123) in calls
    assert result["steps"]["backend_execute"]["status"] == "SKIPPED_ALREADY_SENT"
    assert result["steps"]["backend_result_consume"]["status"] == "PROCESSED"


def test_runner_resumes_processed_result_and_sends_pending_answer(monkeypatch):
    from app.workers import withdrawal_backend_smoke_runner as runner

    calls = []

    class FakeRepository:
        async def get_inbound(self, inbound_event_id):
            return {
                "id": inbound_event_id,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "event_id": "event-1",
                "standard_event_type": "MESSAGE_CREATED",
                "processed": 1,
                "ignored": 0,
                "payload_json": {"event": {"text": "提款不了，用户名是 user-a"}},
            }

        async def list_backend_commands(self, inbound_event_id):
            return [{"id": 10, "command_type": "backend.query", "status": "SENT", "payload_json": {}}]

        async def list_results_for_command(self, command_id):
            return [{"id": 99, "status": "PROCESSED", "result_json": {"status": "success", "answer": "ok"}}]

    async def fail_process_result_by_id(*args, **kwargs):
        raise AssertionError("PROCESSED result must not be consumed again")

    async def fake_sender(pool, sender_client, inbound_event_id, limit=20):
        calls.append(("sender", inbound_event_id))
        return [{"status": "SENT"}]

    monkeypatch.setattr(runner.external_result_consumer, "process_result_by_id", fail_process_result_by_id)
    monkeypatch.setattr(runner.sender_worker, "process_pending_for_inbound_event", fake_sender)

    result = asyncio.run(
        runner.run_smoke(
            pool=object(),
            repository=FakeRepository(),
            inbound_event_id=123,
            execute_backend=True,
            send_livechat=True,
        )
    )

    assert calls == [("sender", 123), ("sender", 123)]
    assert result["steps"]["backend_result_consume"]["status"] == "SKIPPED_ALREADY_PROCESSED"


def test_runner_failed_command_with_failed_result_consumes_safe_failure(monkeypatch):
    from app.workers import withdrawal_backend_smoke_runner as runner

    class FakeRepository:
        async def get_inbound(self, inbound_event_id):
            return {
                "id": inbound_event_id,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "event_id": "event-1",
                "standard_event_type": "MESSAGE_CREATED",
                "processed": 1,
                "ignored": 0,
                "payload_json": {"event": {"text": "提款不了，用户名是 user-a"}},
            }

        async def list_backend_commands(self, inbound_event_id):
            return [{"id": 10, "command_type": "backend.query", "status": "FAILED_BACKEND_QUERY", "payload_json": {}}]

        async def list_results_for_command(self, command_id):
            return [{"id": 99, "status": "PENDING", "result_json": {"status": "failed", "error_code": "FAILED_BACKEND_QUERY"}}]

    async def fake_process_result_by_id(**kwargs):
        return {"id": kwargs["result_id"], "result_type": "backend.query.result", "status": "PROCESSED"}

    monkeypatch.setattr(runner.external_result_consumer, "process_result_by_id", fake_process_result_by_id)

    result = asyncio.run(
        runner.run_smoke(
            pool=object(),
            repository=FakeRepository(),
            inbound_event_id=123,
            execute_backend=True,
        )
    )

    assert result["safe_failure_processed"] is True
    assert result["closed_loop"] is False
    assert result["exit_code"] == 3
    assert result["terminal_status"] == "SAFE_FAILURE_PROCESSED"


def test_runner_sanitizes_secret_output():
    from app.core.settings import Settings
    from app.workers.withdrawal_backend_smoke_runner import sanitize

    safe = sanitize(
        {
            "Authorization": "Bearer secret",
            "message": "token=abc password=def cookie=ghi https://backend.secret.example/path?token=abc 正常中文回答",
        },
        settings=Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_base_url="https://backend.secret.example",
        ),
    )

    rendered = str(safe)
    assert "secret" not in rendered
    assert "backend.secret.example" not in rendered
    assert "token=abc" not in rendered
    assert "password=def" not in rendered
    assert "正常中文回答" in rendered
    assert safe["Authorization"] == "<redacted>"


def test_runner_exit_code_for_closed_loop_success():
    from app.workers.withdrawal_backend_smoke_runner import apply_terminal_status

    result = apply_terminal_status({"smoke_status": "BACKEND_ANSWER_SENT", "closed_loop": True, "failure_reasons": []})

    assert result["exit_code"] == 0
    assert result["terminal_status"] == "SUCCESS"


def test_runner_exit_code_for_sop_not_triggered():
    from app.workers.withdrawal_backend_smoke_runner import apply_terminal_status

    result = apply_terminal_status({"smoke_status": "SOP_NOT_TRIGGERED", "closed_loop": False, "failure_reasons": []})

    assert result["exit_code"] == 1
    assert result["terminal_status"] == "FAILED"
