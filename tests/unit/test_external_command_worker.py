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
