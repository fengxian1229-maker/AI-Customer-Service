import asyncio


def test_external_command_worker_cli_accepts_once_limit_and_dry_run():
    from app.workers.external_command_worker import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--limit", "20", "--dry-run"])

    assert args.once is True
    assert args.limit == 20
    assert args.dry_run is True


def test_external_command_worker_cli_accepts_emit_result():
    from app.workers.external_command_worker import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--limit", "20", "--dry-run", "--emit-result"])

    assert args.emit_result is True


def test_external_command_worker_cli_accepts_execute_human_handoff():
    from app.workers.external_command_worker import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--execute-human-handoff"])

    assert args.execute_human_handoff is True


def test_external_command_worker_cli_accepts_lease_options():
    from app.workers.external_command_worker import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--once", "--limit", "20", "--dry-run", "--worker-id", "local-dev-1", "--lease-seconds", "60", "--max-retries", "5"]
    )

    assert args.worker_id == "local-dev-1"
    assert args.lease_seconds == 60
    assert args.max_retries == 5
    assert args.recover_interval_seconds == 30


def test_external_command_worker_cli_accepts_recover_interval():
    from app.workers.external_command_worker import build_arg_parser

    args = build_arg_parser().parse_args(["--recover-interval-seconds", "0"])

    assert args.recover_interval_seconds == 0


def test_external_command_worker_recovery_disabled_does_not_call_repository():
    from app.workers.external_command_worker import maybe_recover_expired_leases

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


def test_external_command_worker_recovery_runs_when_interval_elapsed():
    from app.workers.external_command_worker import maybe_recover_expired_leases

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


def test_external_command_worker_recovery_failure_is_logged_and_does_not_raise(caplog):
    from app.workers.external_command_worker import maybe_recover_expired_leases

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
    assert "Failed to recover expired external_command leases." in caplog.text


def test_external_command_worker_dry_run_marks_pending_done():
    from app.workers.external_command_worker import process_pending_commands

    class FakeRepository:
        def __init__(self) -> None:
            self.done = []
            self.leased = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.leased.append((limit, worker_id, lease_seconds))
            return [
                {"id": 1, "command_type": "telegram.send_case_card", "payload_json": {"x": 1}},
                {"id": 2, "command_type": "backend.query", "payload_json": {"y": 2}},
            ]

        async def mark_dry_run_done(self, command_id: int) -> None:
            self.done.append(command_id)

    repository = FakeRepository()

    result = asyncio.run(
        process_pending_commands(repository, limit=20, dry_run=True, emit_result=False, worker_id="worker-a", lease_seconds=30)
    )

    assert repository.leased == [(20, "worker-a", 30)]
    assert repository.done == [1, 2]
    assert result == [
        {"id": 1, "command_type": "telegram.send_case_card", "status": "DRY_RUN_DONE"},
        {"id": 2, "command_type": "backend.query", "status": "DRY_RUN_DONE"},
    ]


def test_external_command_worker_rejects_no_execution_mode_before_lease():
    from app.workers.external_command_worker import process_pending_commands

    class FakeRepository:
        def __init__(self) -> None:
            self.leased = False

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.leased = True
            raise AssertionError("must not lease without an execution mode")

    repository = FakeRepository()

    try:
        asyncio.run(process_pending_commands(repository, dry_run=False, execute_human_handoff=False))
    except ValueError as exc:
        assert "must pass either --dry-run or --execute-human-handoff" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert repository.leased is False


def test_external_command_worker_polling_loop_rejects_no_execution_mode_before_lease():
    from app.workers.external_command_worker import run_polling_loop

    class FakeRepository:
        async def recover_expired_leases(self):
            raise AssertionError("must not recover without an execution mode")

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            raise AssertionError("must not lease without an execution mode")

    try:
        asyncio.run(
            run_polling_loop(
                repository=FakeRepository(),
                result_repository=None,
                poll_seconds=5,
                limit=20,
                dry_run=False,
                execute_human_handoff=False,
                iterations=1,
            )
        )
    except ValueError as exc:
        assert "must pass either --dry-run or --execute-human-handoff" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_external_command_worker_run_once_rejects_no_execution_mode_before_pool(monkeypatch):
    from app.workers import external_command_worker

    async def fail_create_pool(settings):
        raise AssertionError("must not create pool without execution mode")

    monkeypatch.setattr(external_command_worker, "create_pool", fail_create_pool)

    try:
        asyncio.run(external_command_worker.run_once(limit=20, dry_run=False, execute_human_handoff=False))
    except ValueError as exc:
        assert "must pass either --dry-run or --execute-human-handoff" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_external_command_worker_human_handoff_dry_run_keeps_mock_behavior():
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.done = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 8,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 18,
                    "command_type": "human_handoff.requested",
                    "payload_json": {},
                }
            ]

        async def mark_dry_run_done(self, command_id: int) -> None:
            self.done.append(command_id)

    class FakeResultRepository:
        def __init__(self) -> None:
            self.inserted = []

        async def insert_idempotent(self, result: dict) -> dict:
            self.inserted.append(result)
            return {"inserted": True, "duplicate": False, "id": 100}

    repository = FakeCommandRepository()
    result_repository = FakeResultRepository()

    result = asyncio.run(
        process_pending_commands(
            repository,
            result_repository=result_repository,
            dry_run=True,
            emit_result=True,
            worker_id="worker-a",
        )
    )

    assert repository.done == [8]
    assert result[0]["status"] == "DRY_RUN_DONE"
    assert result_repository.inserted[0]["result_type"] == "human_handoff.requested.mock_result"
    assert result_repository.inserted[0]["result_json"]["status"] == "MOCKED"


def test_external_command_worker_real_handoff_disabled_does_not_call_livechat():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.statuses = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 9,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 19,
                    "command_type": "human_handoff.requested",
                    "payload_json": {},
                }
            ]

        async def mark_status(self, command_id: int, status: str, error: str | None = None) -> None:
            self.statuses.append((command_id, status, error))

    class SenderClient:
        async def send_text(self, chat_id, thread_id, text):
            raise AssertionError("LiveChat must not be called")

        async def transfer_chat_to_group(self, *args, **kwargs):
            raise AssertionError("LiveChat must not be called")

    repository = FakeCommandRepository()
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=False,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            repository,
            dry_run=False,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: SenderClient(),
            worker_id="worker-a",
        )
    )

    assert repository.statuses == [(9, "SKIPPED_DISABLED", "livechat_handoff_enabled is false")]
    assert result[0]["status"] == "SKIPPED_DISABLED"


def test_external_command_worker_real_handoff_requires_explicit_execute_flag():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.leased = False

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.leased = True
            raise AssertionError("must not lease without explicit execution mode")

    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )
    repository = FakeCommandRepository()

    try:
        asyncio.run(
            process_pending_commands(
                repository,
                dry_run=False,
                execute_human_handoff=False,
                settings=settings,
                worker_id="worker-a",
            )
        )
    except ValueError as exc:
        assert "must pass either --dry-run or --execute-human-handoff" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert repository.leased is False


def test_external_command_worker_real_handoff_success_transfers_emits_result_and_marks_human_active():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.done = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 11,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 21,
                    "command_type": "human_handoff.requested",
                    "payload_json": {},
                }
            ]

        async def mark_sent(self, command_id: int) -> None:
            self.done.append(command_id)

    class FakeResultRepository:
        def __init__(self) -> None:
            self.inserted = []

        async def insert_idempotent(self, result: dict) -> dict:
            self.inserted.append(result)
            return {"inserted": True, "duplicate": False, "id": 101}

    class FakeConversationRepository:
        def __init__(self) -> None:
            self.updated = []

        async def update_workflow_state(self, conversation_id: str, graph_state: dict) -> None:
            self.updated.append((conversation_id, graph_state))

    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(self, chat_id, thread_id, text):
            self.calls.append(("send_text", chat_id, thread_id, text))
            return {"event_id": "notice-1"}

        async def transfer_chat_to_group(self, chat_id, group_id, ignore_agents_availability=True, ignore_requester_presence=True):
            self.calls.append(
                (
                    "transfer",
                    chat_id,
                    group_id,
                    ignore_agents_availability,
                    ignore_requester_presence,
                )
            )
            return {}

    sender_client = SenderClient()
    result_repository = FakeResultRepository()
    conversation_repository = FakeConversationRepository()
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            FakeCommandRepository(),
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            dry_run=False,
            emit_result=True,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: sender_client,
            worker_id="worker-a",
        )
    )

    assert sender_client.calls == [
        ("send_text", "chat-1", "thread-1", "我会为你转接真人客服继续协助。"),
        ("transfer", "chat-1", 23, True, True),
    ]
    assert result[0]["status"] == "SENT"
    assert result_repository.inserted[0]["result_type"] == "human_handoff.transfer_chat.result"
    assert result_repository.inserted[0]["result_json"]["status"] == "TRANSFERRED"
    assert result_repository.inserted[0]["result_json"]["livechat_response"] == {}
    assert result_repository.inserted[0]["status"] == "PROCESSED"
    assert conversation_repository.updated == [
        (
            "livechat:chat-1",
            {
                "status": "HUMAN_ACTIVE",
                "active_workflow": "human_handoff",
                "workflow_stage": "transferred",
                "slot_memory": {},
            },
        )
    ]


def test_external_command_worker_real_handoff_classifies_livechat_errors():
    from app.channels.livechat.sender_client import LiveChatApiError
    from app.core.settings import Settings
    from app.workers.external_command_worker import classify_handoff_error, process_pending_commands

    assert classify_handoff_error(LiveChatApiError(403, {"error": "denied"})) == "FAILED_CONFIG"
    assert classify_handoff_error(LiveChatApiError(429, {"error": "rate"})) == "RETRYABLE"
    assert classify_handoff_error(TimeoutError("timed out")) == "RETRYABLE"
    assert classify_handoff_error(LiveChatApiError(400, {"error": "chat is not active"})) == "FAILED_BUSINESS"

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.statuses = []
            self.processing_failures = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 12,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 22,
                    "command_type": "human_handoff.requested",
                    "payload_json": {},
                }
            ]

        async def mark_status(self, command_id: int, status: str, error: str | None = None) -> None:
            self.statuses.append((command_id, status, error))

        async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> None:
            self.processing_failures.append((command_id, error, max_retries))

    class SenderClient:
        async def send_text(self, chat_id, thread_id, text):
            return {}

        async def transfer_chat_to_group(self, *args, **kwargs):
            raise LiveChatApiError(429, {"error": "rate limited"})

    repository = FakeCommandRepository()
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            repository,
            dry_run=False,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: SenderClient(),
            worker_id="worker-a",
        )
    )

    assert repository.processing_failures[0][0] == 12
    assert repository.processing_failures[0][2] == 3
    assert repository.statuses == []
    assert result[0]["status"] == "RETRYABLE"


def test_external_command_worker_retryable_uses_max_retries_processing_failure():
    from app.channels.livechat.sender_client import LiveChatApiError
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.processing_failures = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 13,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 23,
                    "command_type": "human_handoff.requested",
                    "payload_json": {"human_handoff_stage": {"notice_sent": True}},
                }
            ]

        async def merge_payload_json(self, command_id: int, patch: dict) -> None:
            pass

        async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> None:
            self.processing_failures.append((command_id, error, max_retries))

    class SenderClient:
        def __init__(self) -> None:
            self.notice_calls = 0

        async def send_text(self, chat_id, thread_id, text):
            self.notice_calls += 1

        async def transfer_chat_to_group(self, *args, **kwargs):
            raise LiveChatApiError(429, {"error": "rate limited"})

    sender_client = SenderClient()
    repository = FakeCommandRepository()
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            repository,
            dry_run=False,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: sender_client,
            worker_id="worker-a",
            max_retries=2,
        )
    )

    assert sender_client.notice_calls == 0
    assert repository.processing_failures[0][0] == 13
    assert repository.processing_failures[0][2] == 2
    assert result[0]["status"] == "RETRYABLE"


def test_external_command_worker_retryable_reaches_terminal_after_max_retries_in_repository_style():
    from app.channels.livechat.sender_client import LiveChatApiError
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.row = {
                "id": 16,
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "inbound_event_id": 26,
                "command_type": "human_handoff.requested",
                "payload_json": {"human_handoff_stage": {"notice_sent": True}},
                "status": "RETRYABLE",
                "retry_count": 1,
            }

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [dict(self.row)] if self.row["status"] in {"PENDING", "RETRYABLE"} else []

        async def merge_payload_json(self, command_id: int, patch: dict) -> None:
            pass

        async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> None:
            self.row["retry_count"] += 1
            self.row["status"] = "FAILED" if self.row["retry_count"] >= max_retries else "RETRYABLE"
            self.row["last_error"] = error

    class SenderClient:
        async def send_text(self, chat_id, thread_id, text):
            raise AssertionError("notice is already sent")

        async def transfer_chat_to_group(self, *args, **kwargs):
            raise LiveChatApiError(429, {"error": "rate limited"})

    repository = FakeCommandRepository()
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            repository,
            dry_run=False,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: SenderClient(),
            worker_id="worker-a",
            max_retries=2,
        )
    )
    second = asyncio.run(
        process_pending_commands(
            repository,
            dry_run=False,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: SenderClient(),
            worker_id="worker-b",
            max_retries=2,
        )
    )

    assert result[0]["status"] == "FAILED"
    assert repository.row["status"] == "FAILED"
    assert repository.row["retry_count"] == 2
    assert second == []


def test_external_command_worker_run_once_summary_counts_failed_retryable_skipped_and_blocked(monkeypatch):
    from app.workers import external_command_worker

    class FakeSettings:
        poll_seconds = 5

        def __init__(self, **kwargs) -> None:
            pass

    class FakePool:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def fake_create_pool(settings):
        return FakePool()

    class FakeCommandRepository:
        def __init__(self, pool) -> None:
            pass

    class FakeResultRepository:
        def __init__(self, pool) -> None:
            pass

    class FakeConversationRepository:
        def __init__(self, pool) -> None:
            pass

    async def fake_process_pending_commands(*args, **kwargs):
        return [
            {"id": 1, "status": "FAILED_CONFIG"},
            {"id": 2, "status": "FAILED_BUSINESS"},
            {"id": 3, "status": "FAILED_UNSUPPORTED"},
            {"id": 4, "status": "SKIPPED_DISABLED"},
            {"id": 5, "status": "RETRYABLE"},
            {"id": 6, "status": "SENT"},
            {"id": 7, "status": "DRY_RUN_DONE", "result_insert": {"inserted": True}},
            {"id": 8, "status": "FAILED"},
        ]

    monkeypatch.setattr(external_command_worker, "Settings", FakeSettings)
    monkeypatch.setattr(external_command_worker, "create_pool", fake_create_pool)
    monkeypatch.setattr(external_command_worker, "ExternalCommandRepository", FakeCommandRepository)
    monkeypatch.setattr(external_command_worker, "ExternalCommandResultRepository", FakeResultRepository)
    monkeypatch.setattr(external_command_worker, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(external_command_worker, "process_pending_commands", fake_process_pending_commands)

    result = asyncio.run(external_command_worker.run_once(limit=20, dry_run=True, emit_result=True))

    assert result["processed"] == 8
    assert result["failed"] == 4
    assert result["terminal_failed"] == 4
    assert result["retryable"] == 1
    assert result["skipped"] == 1
    assert result["blocked"] == 1
    assert result["sent"] == 1
    assert result["dry_run_done"] == 1


def test_external_command_worker_transfer_success_conversation_update_failure_is_terminal_manual_review():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.statuses = []
            self.stages = []
            self.processing_failures = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 14,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 24,
                    "command_type": "human_handoff.requested",
                    "payload_json": {},
                }
            ]

        async def merge_payload_json(self, command_id: int, patch: dict) -> None:
            self.stages.append((command_id, patch["human_handoff_stage"]))

        async def mark_status(self, command_id: int, status: str, error: str | None = None) -> None:
            self.statuses.append((command_id, status, error))

        async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> None:
            self.processing_failures.append((command_id, error, max_retries))

    class FakeConversationRepository:
        async def update_workflow_state(self, conversation_id: str, graph_state: dict) -> None:
            raise RuntimeError("db update failed")

    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(self, chat_id, thread_id, text):
            self.calls.append("notice")

        async def transfer_chat_to_group(self, *args, **kwargs):
            self.calls.append("transfer")
            return {"ok": True}

    repository = FakeCommandRepository()
    sender_client = SenderClient()
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            repository,
            conversation_repository=FakeConversationRepository(),
            dry_run=False,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: sender_client,
            worker_id="worker-a",
        )
    )

    assert sender_client.calls == ["notice", "transfer"]
    assert repository.stages[-1][1]["transfer_succeeded"] is True
    assert repository.statuses[0][0:2] == (14, "FAILED_AFTER_EXTERNAL_SUCCESS")
    assert "LiveChat transfer may have succeeded" in repository.statuses[0][2]
    assert repository.processing_failures == []
    assert result[0]["status"] == "FAILED_AFTER_EXTERNAL_SUCCESS"


def test_external_command_worker_transfer_success_result_insert_failure_is_terminal_manual_review():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.statuses = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 15,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 25,
                    "command_type": "human_handoff.requested",
                    "payload_json": {},
                }
            ]

        async def merge_payload_json(self, command_id: int, patch: dict) -> None:
            pass

        async def mark_status(self, command_id: int, status: str, error: str | None = None) -> None:
            self.statuses.append((command_id, status, error))

        async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> None:
            raise AssertionError("must not mark retryable after external success")

    class FakeConversationRepository:
        async def update_workflow_state(self, conversation_id: str, graph_state: dict) -> None:
            pass

    class FakeResultRepository:
        async def insert_idempotent(self, result: dict) -> dict:
            raise RuntimeError("result insert failed")

    class SenderClient:
        async def send_text(self, chat_id, thread_id, text):
            pass

        async def transfer_chat_to_group(self, *args, **kwargs):
            return {}

    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_enabled=True,
        livechat_handoff_target_group_id=23,
    )

    result = asyncio.run(
        process_pending_commands(
            FakeCommandRepository(),
            result_repository=FakeResultRepository(),
            conversation_repository=FakeConversationRepository(),
            dry_run=False,
            emit_result=True,
            execute_human_handoff=True,
            settings=settings,
            sender_client_factory=lambda settings: SenderClient(),
            worker_id="worker-a",
        )
    )

    assert result[0]["status"] == "FAILED_AFTER_EXTERNAL_SUCCESS"


def test_external_command_worker_emit_result_inserts_mock_results_idempotently():
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.done = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [
                {
                    "id": 1,
                    "tenant_id": "default",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 11,
                    "command_type": "backend.query",
                    "payload_json": {"account_or_phone": "andy123"},
                }
            ]

        async def mark_dry_run_done(self, command_id: int) -> None:
            self.done.append(command_id)

    class FakeResultRepository:
        def __init__(self) -> None:
            self.inserted = []

        async def insert_idempotent(self, result: dict) -> dict:
            self.inserted.append(result)
            return {"inserted": True, "duplicate": False, "id": 99}

    command_repository = FakeCommandRepository()
    result_repository = FakeResultRepository()

    result = asyncio.run(
        process_pending_commands(
            command_repository,
            result_repository=result_repository,
            limit=20,
            dry_run=True,
            emit_result=True,
            worker_id="worker-a",
        )
    )

    assert command_repository.done == [1]
    assert result_repository.inserted[0]["external_command_id"] == 1
    assert result_repository.inserted[0]["result_type"] == "backend.query.result"
    assert result_repository.inserted[0]["result_json"]["status"] == "success"
    assert result[0]["result_insert"] == {"inserted": True, "duplicate": False, "id": 99}


def test_external_command_worker_marks_processing_failed_on_error():
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self) -> None:
            self.failures = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            return [{"id": 3, "command_type": "unsupported.command", "payload_json": {}}]

        async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> None:
            self.failures.append((command_id, error, max_retries))

    repository = FakeCommandRepository()

    result = asyncio.run(
        process_pending_commands(repository, limit=20, dry_run=True, worker_id="worker-a", max_retries=2)
    )

    assert repository.failures == [(3, "unsupported command_type: unsupported.command", 2)]
    assert result[0]["status"] == "FAILED"


def test_external_command_worker_two_worker_leases_do_not_overlap():
    from app.workers.external_command_worker import process_pending_commands

    class InMemoryLeaseRepository:
        def __init__(self) -> None:
            self.rows = [
                {"id": 1, "command_type": "backend.query", "payload_json": {}, "locked_by": None},
            ]
            self.done = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            available = [row for row in self.rows if row["locked_by"] is None][:limit]
            for row in available:
                row["locked_by"] = worker_id
            return available

        async def mark_dry_run_done(self, command_id: int) -> None:
            self.done.append(command_id)

    repository = InMemoryLeaseRepository()

    first = asyncio.run(process_pending_commands(repository, limit=20, dry_run=True, worker_id="worker-a"))
    second = asyncio.run(process_pending_commands(repository, limit=20, dry_run=True, worker_id="worker-b"))

    assert [item["id"] for item in first] == [1]
    assert second == []


def test_external_command_worker_polling_loop_sleeps_then_processes_second_round():
    from app.workers.external_command_worker import run_polling_loop

    class FakeRepository:
        def __init__(self) -> None:
            self.round = 0
            self.done = []
            self.recovered = 0

        async def recover_expired_leases(self):
            self.recovered += 1
            return 0

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.round += 1
            if self.round == 1:
                return []
            return [{"id": 10, "command_type": "backend.query", "payload_json": {}}]

        async def mark_dry_run_done(self, command_id: int):
            self.done.append(command_id)

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    repository = FakeRepository()

    asyncio.run(
        run_polling_loop(
            repository=repository,
            result_repository=None,
            poll_seconds=5,
            limit=20,
            dry_run=True,
            worker_id="worker-a",
            recover_interval_seconds=0,
            iterations=2,
            sleep=fake_sleep,
        )
    )

    assert sleeps == [5]
    assert repository.done == [10]
    assert repository.recovered == 0


def test_external_command_worker_polling_loop_recovery_failure_continues(caplog):
    from app.workers.external_command_worker import run_polling_loop

    class FakeRepository:
        def __init__(self) -> None:
            self.recovery_calls = 0
            self.lease_calls = 0
            self.done = []

        async def recover_expired_leases(self):
            self.recovery_calls += 1
            if self.recovery_calls == 1:
                raise RuntimeError("recover failed")
            return 0

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            self.lease_calls += 1
            return [{"id": 11, "command_type": "backend.query", "payload_json": {}}] if self.lease_calls == 2 else []

        async def mark_dry_run_done(self, command_id: int):
            self.done.append(command_id)

    async def fake_sleep(seconds):
        return None

    repository = FakeRepository()

    asyncio.run(
        run_polling_loop(
            repository=repository,
            result_repository=None,
            poll_seconds=5,
            limit=20,
            dry_run=True,
            worker_id="worker-a",
            recover_interval_seconds=1,
            last_recovered_at=-10.0,
            iterations=2,
            sleep=fake_sleep,
        )
    )

    assert "Failed to recover expired external_command leases." in caplog.text
    assert repository.done == [11]


def test_external_command_worker_crash_recovery_does_not_increment_retry_count():
    from app.workers.external_command_worker import process_pending_commands

    class InMemoryRepository:
        def __init__(self) -> None:
            self.row = {"id": 12, "command_type": "backend.query", "payload_json": {}, "locked_by": None, "expired": False}
            self.retry_count = 0
            self.done = []

        async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int):
            if self.row["locked_by"] is None or self.row["expired"]:
                self.row["locked_by"] = worker_id
                self.row["expired"] = False
                return [dict(self.row)]
            return []

        async def recover_expired_leases(self):
            if self.row["locked_by"] and self.row["expired"]:
                self.row["locked_by"] = None
                return 1
            return 0

        async def mark_dry_run_done(self, command_id: int):
            self.done.append(command_id)

    repository = InMemoryRepository()

    first = asyncio.run(repository.lease_pending(limit=1, worker_id="worker-a", lease_seconds=1))
    repository.row["expired"] = True
    recovered = asyncio.run(repository.recover_expired_leases())
    second = asyncio.run(process_pending_commands(repository, limit=1, dry_run=True, worker_id="worker-b"))

    assert [row["id"] for row in first] == [12]
    assert recovered == 1
    assert [item["id"] for item in second] == [12]
    assert repository.retry_count == 0
    assert repository.done == [12]
