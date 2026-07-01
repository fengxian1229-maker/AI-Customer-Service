import asyncio


def complete_snapshot():
    answer = "当前未查询到未完成流水要求。"
    return {
        "inbound_event": {"id": 11, "processed": 1},
        "conversation_state": {"status": "AI_ACTIVE", "active_workflow": None, "workflow_stage": "completed"},
        "outbound_messages": [{"id": 2, "status": "SENT", "payload_json": {"text": answer}}],
        "external_commands": [{"id": 5, "command_type": "backend.query", "status": "SENT"}],
        "external_command_results": [
            {
                "id": 7,
                "external_command_id": 5,
                "result_type": "backend.query.result",
                "status": "PROCESSED",
                "result_json": {"status": "success", "answer": answer},
            }
        ],
        "conversation_messages": [
            {"sender_role": "backend", "text_content": "后台查询成功"},
            {"sender_role": "assistant", "text_content": answer},
        ],
        "graph_run_errors": [],
        "graph_checkpoints": [],
    }


def test_backend_sop_smoke_admin_infers_closed_loop_success():
    from app.workers.backend_sop_smoke_admin import assert_closed_loop, infer_smoke_status

    snapshot = complete_snapshot()

    assert infer_smoke_status(snapshot) == "BACKEND_ANSWER_SENT"
    result = assert_closed_loop(snapshot)
    assert result["closed_loop"] is True
    assert result["smoke_status"] == "BACKEND_ANSWER_SENT"


def test_backend_sop_smoke_admin_identifies_backend_answer_by_message_kind():
    from app.workers.backend_sop_smoke_admin import assert_closed_loop, infer_smoke_status

    snapshot = complete_snapshot()
    answer = snapshot["external_command_results"][0]["result_json"]["answer"]
    snapshot["outbound_messages"] = [
        {"id": 1, "status": "SENT", "message_kind": "text", "payload_json": {"text": "即时回复"}},
        {
            "id": 2,
            "status": "SENT",
            "message_kind": "backend_answer",
            "command_type": "backend.query.result",
            "dedup_key": "default:livechat:chat-1:11:backend.query.result:7",
            "payload_json": {"text": answer},
        },
    ]

    assert infer_smoke_status(snapshot) == "BACKEND_ANSWER_SENT"
    assert assert_closed_loop(snapshot)["closed_loop"] is True


def test_backend_sop_smoke_admin_assert_closed_loop_reports_missing_step():
    from app.workers.backend_sop_smoke_admin import assert_closed_loop, infer_smoke_status

    snapshot = complete_snapshot()
    snapshot["external_command_results"] = []

    assert infer_smoke_status(snapshot) == "BACKEND_COMMAND_SENT"
    result = assert_closed_loop(snapshot)
    assert result["closed_loop"] is False
    assert any("backend.query.result" in reason for reason in result["failure_reasons"])


def test_backend_sop_smoke_admin_sanitizes_secret_fields():
    from app.workers.backend_sop_smoke_admin import sanitize

    result = sanitize(
        {
            "Authorization": "Bearer secret",
            "nested": {"backend_login_password": "secret-password", "message": "token=abc password=def"},
        }
    )

    rendered = str(result)
    assert "secret-password" not in rendered
    assert "token=abc" not in rendered
    assert result["Authorization"] == "<redacted>"


def test_backend_sop_smoke_admin_parser_accepts_commands():
    from app.workers.backend_sop_smoke_admin import build_arg_parser

    latest = build_arg_parser().parse_args(["latest", "--chat-id", "chat-1"])
    by_inbound = build_arg_parser().parse_args(["by-inbound", "--inbound-event-id", "11"])
    assert_loop = build_arg_parser().parse_args(["assert-closed-loop", "--inbound-event-id", "11"])

    assert latest.command == "latest"
    assert latest.chat_id == "chat-1"
    assert by_inbound.command == "by-inbound"
    assert by_inbound.inbound_event_id == 11
    assert assert_loop.command == "assert-closed-loop"
    assert assert_loop.inbound_event_id == 11


def test_backend_sop_smoke_admin_parser_accepts_latest_backend():
    from app.workers.backend_sop_smoke_admin import build_arg_parser

    latest_backend = build_arg_parser().parse_args(["latest-backend", "--chat-id", "chat-1"])

    assert latest_backend.command == "latest-backend"
    assert latest_backend.chat_id == "chat-1"


def test_backend_sop_smoke_admin_run_command_latest_backend_uses_repository(monkeypatch):
    from app.workers import backend_sop_smoke_admin

    calls = {}

    class FakeSettings:
        def __init__(self, **kwargs):
            calls["settings_kwargs"] = kwargs

    class FakePool:
        def close(self):
            calls["closed"] = True

        async def wait_closed(self):
            calls["wait_closed"] = True

    class FakeRepository:
        def __init__(self, pool):
            calls["repo_pool"] = pool

        async def latest_backend(self, chat_id=None, conversation_id=None, limit=20):
            calls["latest_backend"] = (chat_id, conversation_id, limit)
            return complete_snapshot()

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    monkeypatch.setattr(backend_sop_smoke_admin, "Settings", FakeSettings)
    monkeypatch.setattr(backend_sop_smoke_admin, "create_pool", fake_create_pool)
    monkeypatch.setattr(backend_sop_smoke_admin, "BackendSopSmokeReadRepository", FakeRepository)

    result = asyncio.run(backend_sop_smoke_admin.run_command("latest-backend", chat_id="chat-1", limit=9))

    assert calls["latest_backend"] == ("chat-1", None, 9)
    assert result["smoke_status"] == "BACKEND_ANSWER_SENT"
    assert calls["closed"] is True
    assert calls["wait_closed"] is True


def test_backend_sop_smoke_admin_run_command_uses_repository_and_asserts(monkeypatch):
    from app.workers import backend_sop_smoke_admin

    calls = {}

    class FakeSettings:
        def __init__(self, **kwargs):
            calls["settings_kwargs"] = kwargs

    class FakePool:
        def close(self):
            calls["closed"] = True

        async def wait_closed(self):
            calls["wait_closed"] = True

    class FakeRepository:
        def __init__(self, pool):
            calls["repo_pool"] = pool

        async def by_inbound(self, inbound_event_id):
            calls["inbound_event_id"] = inbound_event_id
            return complete_snapshot()

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    monkeypatch.setattr(backend_sop_smoke_admin, "Settings", FakeSettings)
    monkeypatch.setattr(backend_sop_smoke_admin, "create_pool", fake_create_pool)
    monkeypatch.setattr(backend_sop_smoke_admin, "BackendSopSmokeReadRepository", FakeRepository)

    result = asyncio.run(backend_sop_smoke_admin.run_command("assert-closed-loop", inbound_event_id=11))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-backend-sop-smoke-admin",
        "livechat_account_id": "unused-for-backend-sop-smoke-admin",
    }
    assert calls["inbound_event_id"] == 11
    assert result["closed_loop"] is True
    assert calls["closed"] is True
    assert calls["wait_closed"] is True
