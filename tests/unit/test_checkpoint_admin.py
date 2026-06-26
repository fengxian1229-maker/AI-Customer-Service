import asyncio


def test_checkpoint_admin_parser_accepts_list_filters():
    from app.workers.checkpoint_admin import build_arg_parser

    args = build_arg_parser().parse_args(
        [
            "list-runs",
            "--conversation-id",
            "livechat:chat-1",
            "--graph-thread-id",
            "livechat:chat-1",
            "--inbound-event-id",
            "11",
            "--status",
            "FAILED",
            "--created-after",
            "2026-06-25 00:00:00",
            "--created-before",
            "2026-06-27 00:00:00",
            "--limit",
            "5",
        ]
    )

    assert args.command == "list-runs"
    assert args.conversation_id == "livechat:chat-1"
    assert args.graph_thread_id == "livechat:chat-1"
    assert args.inbound_event_id == 11
    assert args.status == "FAILED"
    assert args.created_after == "2026-06-25 00:00:00"
    assert args.created_before == "2026-06-27 00:00:00"
    assert args.limit == 5


def test_checkpoint_admin_run_command_returns_empty_payloads():
    from app.workers import checkpoint_admin

    class FakeCheckpointRunRepository:
        async def list_runs(self, **kwargs):
            return []

        async def get_run(self, run_id: int):
            return None

        async def fetch_latest(self, **kwargs):
            return None

    class FakeGraphRunErrorRepository:
        async def list_errors(self, **kwargs):
            return []

    list_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["list-runs", "--conversation-id", "livechat:chat-1"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )
    show_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["show-run", "--run-id", "66"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )
    latest_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["latest", "--conversation-id", "livechat:chat-1"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )
    error_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["errors", "--conversation-id", "livechat:chat-1"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )

    assert list_result["runs"] == []
    assert show_result["run"] is None
    assert latest_result["run"] is None
    assert error_result["errors"] == []


def test_checkpoint_admin_run_command_dispatches_repositories():
    from app.workers import checkpoint_admin

    calls = []

    class FakeCheckpointRunRepository:
        async def list_runs(self, **kwargs):
            calls.append(("list-runs", kwargs))
            return [{"id": 66, "status": "FAILED"}]

        async def get_run(self, run_id: int):
            calls.append(("show-run", run_id))
            return {"id": run_id, "status": "FAILED", "error_message": "boom"}

        async def fetch_latest(self, **kwargs):
            calls.append(("latest", kwargs))
            return {"id": 67, "status": "SUCCEEDED"}

    class FakeGraphRunErrorRepository:
        async def list_errors(self, **kwargs):
            calls.append(("errors", kwargs))
            return [{"conversation_id": "livechat:chat-1", "error_type": "RuntimeError"}]

    list_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(
                ["list-runs", "--conversation-id", "livechat:chat-1", "--status", "FAILED", "--limit", "5"]
            ),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )
    show_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["show-run", "--run-id", "66"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )
    latest_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["latest", "--graph-thread-id", "livechat:chat-1"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )
    error_result = asyncio.run(
        checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["errors", "--conversation-id", "livechat:chat-1", "--limit", "3"]),
            FakeCheckpointRunRepository(),
            FakeGraphRunErrorRepository(),
        )
    )

    assert list_result["runs"] == [{"id": 66, "status": "FAILED"}]
    assert show_result["run"]["error_message"] == "boom"
    assert latest_result["run"] == {"id": 67, "status": "SUCCEEDED"}
    assert error_result["errors"] == [{"conversation_id": "livechat:chat-1", "error_type": "RuntimeError"}]
    assert calls == [
        (
            "list-runs",
            {
                "conversation_id": "livechat:chat-1",
                "graph_thread_id": None,
                "inbound_event_id": None,
                "status": "FAILED",
                "created_after": None,
                "created_before": None,
                "limit": 5,
            },
        ),
        ("show-run", 66),
        (
            "latest",
            {
                "conversation_id": None,
                "graph_thread_id": "livechat:chat-1",
                "inbound_event_id": None,
                "status": None,
                "created_after": None,
                "created_before": None,
            },
        ),
        (
            "errors",
            {
                "conversation_id": "livechat:chat-1",
                "graph_thread_id": None,
                "inbound_event_id": None,
                "created_after": None,
                "created_before": None,
                "limit": 3,
            },
        ),
    ]
