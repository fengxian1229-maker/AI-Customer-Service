import pytest


def test_parse_polling_groups_prefers_cli_groups():
    from app.workers.polling_receiver import parse_group_ids

    assert parse_group_ids("23,0", env_value="15") == {23, 0}


def test_parse_polling_groups_reads_environment_when_cli_missing():
    from app.workers.polling_receiver import parse_group_ids

    assert parse_group_ids(None, env_value="23") == {23}


def test_parse_polling_groups_rejects_missing_groups():
    from app.workers.polling_receiver import parse_group_ids

    with pytest.raises(ValueError, match="Refusing to poll LiveChat without explicit groups"):
        parse_group_ids(None, env_value=None)


def test_polling_cli_parser_accepts_once_limit_and_groups():
    from app.workers.polling_receiver import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--groups", "23", "--limit", "20"])

    assert args.once is True
    assert args.groups == "23"
    assert args.limit == 20


def test_polling_cli_parser_accepts_simple_loop_options():
    from app.workers.polling_receiver import build_arg_parser

    args = build_arg_parser().parse_args([
        "--groups",
        "23",
        "--sleep-seconds",
        "0.5",
        "--max-iterations",
        "2",
    ])

    assert args.once is False
    assert args.sleep_seconds == 0.5
    assert args.max_iterations == 2


def test_polling_run_loop_executes_max_iterations_with_sleep():
    import asyncio

    from app.workers.polling_receiver import run_polling_loop

    calls = {"cycles": 0, "sleeps": []}

    async def fake_run_once(limit: int, groups: set[int]):
        calls["cycles"] += 1
        return {
            "worker": "polling_receiver",
            "mode": "once",
            "groups": sorted(groups),
            "listed": limit,
            "matched_group": 1,
            "inserted": 1,
            "duplicates": 0,
            "ignored": 0,
            "ignored_self": 0,
            "ignored_agent": 0,
            "ignored_group": 0,
        }

    async def fake_sleep(seconds: float):
        calls["sleeps"].append(seconds)

    results = asyncio.run(
        run_polling_loop(
            limit=20,
            groups={23},
            sleep_seconds=0.25,
            max_iterations=2,
            run_once_func=fake_run_once,
            sleep_func=fake_sleep,
        )
    )

    assert calls["cycles"] == 2
    assert calls["sleeps"] == [0.25]
    assert [result["mode"] for result in results] == ["loop", "loop"]


def test_gateway_cli_parser_accepts_once_and_limit():
    from app.workers.gateway_consumer import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--limit", "20"])

    assert args.once is True
    assert args.limit == 20


def test_faq_smoke_admin_parser_accepts_filters():
    from app.workers.faq_smoke_admin import build_arg_parser

    args = build_arg_parser().parse_args([
        "summary",
        "--conversation-id",
        "livechat:chat-1",
        "--chat-id",
        "chat-1",
        "--inbound-event-id",
        "11",
        "--limit",
        "5",
    ])

    assert args.command == "summary"
    assert args.conversation_id == "livechat:chat-1"
    assert args.chat_id == "chat-1"
    assert args.inbound_event_id == 11
    assert args.limit == 5


def test_faq_smoke_admin_run_command_is_read_only_and_uses_unused_livechat_credentials(monkeypatch):
    import asyncio

    from app.workers import faq_smoke_admin

    calls = {}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs

    class FakePool:
        def close(self) -> None:
            calls["closed"] = True

        async def wait_closed(self) -> None:
            calls["wait_closed"] = True

    class FakeRepository:
        def __init__(self, pool) -> None:
            calls["repository_pool"] = pool

        async def latest_inbound(self, **kwargs):
            calls["query_kwargs"] = kwargs
            return [{"text": "怎么存款？"}]

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    monkeypatch.setattr(faq_smoke_admin, "Settings", FakeSettings)
    monkeypatch.setattr(faq_smoke_admin, "create_pool", fake_create_pool)
    monkeypatch.setattr(faq_smoke_admin, "FaqSmokeReadRepository", FakeRepository)

    result = asyncio.run(
        faq_smoke_admin.run_command(
            "latest-inbound",
            conversation_id="livechat:chat-1",
            chat_id="chat-1",
            inbound_event_id=11,
            limit=5,
        )
    )

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-faq-smoke-admin",
        "livechat_account_id": "unused-for-faq-smoke-admin",
    }
    assert calls["query_kwargs"] == {
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "inbound_event_id": 11,
        "limit": 5,
    }
    assert result == [{"text": "怎么存款？"}]
    assert calls["closed"] is True
    assert calls["wait_closed"] is True


def test_llm_shadow_admin_parser_accepts_filters():
    from app.workers.llm_shadow_admin import build_arg_parser

    args = build_arg_parser().parse_args([
        "summary",
        "--conversation-id",
        "livechat:chat-1",
        "--chat-id",
        "chat-1",
        "--limit",
        "5",
    ])

    assert args.command == "summary"
    assert args.conversation_id == "livechat:chat-1"
    assert args.chat_id == "chat-1"
    assert args.limit == 5


def test_llm_shadow_admin_run_command_reads_checkpoint_metadata_and_sanitizes(monkeypatch):
    import asyncio

    from app.workers import llm_shadow_admin

    calls = {}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs

    class FakePool:
        def close(self) -> None:
            calls["closed"] = True

        async def wait_closed(self) -> None:
            calls["wait_closed"] = True

    class FakeRepository:
        def __init__(self, pool) -> None:
            calls["repository_pool"] = pool

        async def list_runs(self, **kwargs):
            calls["query_kwargs"] = kwargs
            return [
                {
                    "id": 91,
                    "conversation_id": "livechat:chat-1",
                    "graph_thread_id": "livechat:chat-1",
                    "status": "SUCCEEDED",
                    "created_at": "2026-06-27 00:00:00.000000",
                    "updated_at": "2026-06-27 00:00:01.000000",
                    "metadata_json": {
                        "llm_shadow": {
                            "rewrite": {"provider": "mock", "status": "ok", "api_key": "hidden"},
                            "intent": {"provider": "mock", "status": "error", "error_type": "RuntimeError"},
                        }
                    },
                }
            ]

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    monkeypatch.setattr(llm_shadow_admin, "Settings", FakeSettings)
    monkeypatch.setattr(llm_shadow_admin, "create_pool", fake_create_pool)
    monkeypatch.setattr(llm_shadow_admin, "GraphCheckpointRunRepository", FakeRepository)

    result = asyncio.run(llm_shadow_admin.run_command("summary", chat_id="chat-1", limit=5))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-llm-shadow-admin",
        "livechat_account_id": "unused-for-llm-shadow-admin",
    }
    assert calls["query_kwargs"] == {"conversation_id": "livechat:chat-1", "limit": 5}
    assert result["total"] == 1
    assert result["error_count"] == 1
    assert result["latest"]["llm_shadow"]["rewrite"] == {"provider": "mock", "status": "ok"}
    assert "api_key" not in str(result)
    assert calls["closed"] is True
    assert calls["wait_closed"] is True


def test_gateway_run_once_does_not_require_livechat_credentials(monkeypatch):
    import asyncio

    from app.workers import gateway_consumer

    calls = {}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs
            self.langgraph_checkpoint_mode = "memory"

    class FakePool:
        def close(self) -> None:
            calls["closed"] = True

        async def wait_closed(self) -> None:
            calls["wait_closed"] = True

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    async def fake_process_next_batch(pool, limit: int = 20, checkpoint_mode: str = "off", settings=None):
        calls["limit"] = limit
        calls["checkpoint_mode"] = checkpoint_mode
        calls["process_settings"] = settings
        return {
            "results": [{"outbound_message": {"id": 1}}],
            "failures": [],
            "processed": 1,
            "failed": 0,
            "enqueued": 1,
        }

    monkeypatch.setattr(gateway_consumer, "Settings", FakeSettings)
    monkeypatch.setattr(gateway_consumer, "create_pool", fake_create_pool)
    monkeypatch.setattr(gateway_consumer, "process_next_batch", fake_process_next_batch)

    result = asyncio.run(gateway_consumer.run_once(limit=20))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-gateway",
        "livechat_account_id": "unused-for-gateway",
    }
    assert calls["limit"] == 20
    assert calls["checkpoint_mode"] == "memory"
    assert calls["closed"] is True
    assert calls["wait_closed"] is True
    assert result["processed"] == 1
    assert result["failed"] == 0
    assert result["enqueued"] == 1


def test_gateway_llm_summary_reports_shadow_and_fallback_flags():
    from app.workers.gateway_consumer import _build_llm_summary

    class FakeSettings:
        llm_provider = "mock"
        llm_rewrite_shadow_enabled = True
        llm_intent_shadow_enabled = True
        llm_rewrite_fallback_enabled = False
        llm_intent_fallback_enabled = False

    summary = _build_llm_summary(FakeSettings())

    assert summary == {
        "provider": "mock",
        "rewrite_shadow_enabled": True,
        "intent_shadow_enabled": True,
        "rewrite_fallback_enabled": False,
        "intent_fallback_enabled": False,
        "fallback_enabled": False,
        "shadow_active": True,
    }


def test_setup_langgraph_checkpoints_skips_when_mode_is_off(monkeypatch):
    import asyncio

    from app.workers import setup_langgraph_checkpoints

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.langgraph_checkpoint_mode = "off"
            self.langgraph_checkpoint_setup_on_start = False
            self.mysql_password = "secret"
            self.mysql_host = "127.0.0.1"
            self.mysql_port = 3306
            self.mysql_user = "root"
            self.mysql_database = "livechat_ai"

        @property
        def mysql_checkpoint_dsn(self) -> str:
            return "mysql://root:secret@127.0.0.1:3306/livechat_ai"

    monkeypatch.setattr(setup_langgraph_checkpoints, "Settings", FakeSettings)

    result = asyncio.run(setup_langgraph_checkpoints.run())

    assert result["worker"] == "setup_langgraph_checkpoints"
    assert result["checkpoint_mode"] == "off"
    assert result["status"] == "skipped"
    assert "secret" not in str(result)


def test_setup_langgraph_checkpoints_mysql_calls_setup(monkeypatch):
    import asyncio

    from app.workers import setup_langgraph_checkpoints

    calls = {"setup": 0, "closed": 0, "preflight": 0}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.langgraph_checkpoint_mode = "mysql"
            self.langgraph_checkpoint_setup_on_start = False
            self.mysql_password = "secret"

        @property
        def mysql_checkpoint_dsn(self) -> str:
            return "mysql://root:secret@127.0.0.1:3306/livechat_ai"

    class FakeManagedCheckpointer:
        def __init__(self) -> None:
            self.checkpointer = self

        def setup(self) -> None:
            calls["setup"] += 1

        def close(self) -> None:
            calls["closed"] += 1

    def fake_build_checkpointer(mode: str, settings=None):
        calls["mode"] = mode
        calls["settings"] = settings
        return FakeManagedCheckpointer()

    async def fake_preflight(settings):
        calls["preflight"] += 1
        return {"engine": "MySQL", "version": "8.0.36"}

    monkeypatch.setattr(setup_langgraph_checkpoints, "Settings", FakeSettings)
    monkeypatch.setattr(setup_langgraph_checkpoints, "build_checkpointer", fake_build_checkpointer)
    monkeypatch.setattr(setup_langgraph_checkpoints, "check_mysql_checkpoint_version", fake_preflight)

    result = asyncio.run(setup_langgraph_checkpoints.run())

    assert result["checkpoint_mode"] == "mysql"
    assert result["setup"] is True
    assert result["status"] == "ok"
    assert result["error_type"] is None
    assert calls["preflight"] == 1
    assert calls["setup"] == 1
    assert calls["closed"] == 1
    assert "secret" not in str(result)


def test_setup_langgraph_checkpoints_returns_error_metadata(monkeypatch):
    import asyncio

    from app.workers import setup_langgraph_checkpoints

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.langgraph_checkpoint_mode = "mysql"
            self.langgraph_checkpoint_setup_on_start = False
            self.mysql_password = "topsecret"

        @property
        def mysql_checkpoint_dsn(self) -> str:
            return "mysql://root:topsecret@127.0.0.1:3306/livechat_ai"

    async def fake_preflight(settings):
        return {"engine": "MySQL", "version": "8.0.36"}

    def fake_build_checkpointer(mode: str, settings=None):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(setup_langgraph_checkpoints, "Settings", FakeSettings)
    monkeypatch.setattr(setup_langgraph_checkpoints, "build_checkpointer", fake_build_checkpointer)
    monkeypatch.setattr(setup_langgraph_checkpoints, "check_mysql_checkpoint_version", fake_preflight)

    result = asyncio.run(setup_langgraph_checkpoints.run())

    assert result["status"] == "error"
    assert result["error_type"] == "RuntimeError"
    assert result["error_message"] == "setup failed"
    assert "topsecret" not in str(result)


def test_sender_cli_parser_accepts_once_and_limit():
    from app.workers.sender_worker import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--limit", "20"])

    assert args.once is True
    assert args.limit == 20


def test_seed_knowledge_run_dry_run_does_not_create_pool(monkeypatch):
    import asyncio

    from app.workers import seed_knowledge

    calls = {"pool": 0}

    async def fake_create_pool(_settings):
        calls["pool"] += 1
        raise AssertionError("create_pool should not be called")

    monkeypatch.setattr(seed_knowledge, "create_pool", fake_create_pool)

    result = asyncio.run(
        seed_knowledge.run(["--tenant-id", "default", "--kb-scope", "default", "--dry-run"])
    )

    assert result["dry_run"] is True
    assert calls["pool"] == 0


def test_knowledge_admin_cli_calls_list_and_get():
    import asyncio

    from app.workers import knowledge_admin

    calls = []

    class FakeRepository:
        async def list_documents(self, tenant_id: str, kb_scope: str = "default", enabled=None, limit: int = 50):
            calls.append(("list", tenant_id, kb_scope, enabled, limit))
            return [{"title": "奖金规则说明"}]

        async def get_by_title(self, tenant_id: str, kb_scope: str, title: str):
            calls.append(("get", tenant_id, kb_scope, title))
            return {"title": title}

    list_result = asyncio.run(
        knowledge_admin.run_command(
            knowledge_admin.build_arg_parser().parse_args(["list", "--tenant-id", "default", "--kb-scope", "default"]),
            FakeRepository(),
        )
    )
    get_result = asyncio.run(
        knowledge_admin.run_command(
            knowledge_admin.build_arg_parser().parse_args(
                ["get", "--tenant-id", "default", "--kb-scope", "default", "--title", "奖金规则说明"]
            ),
            FakeRepository(),
        )
    )

    assert list_result["documents"] == [{"title": "奖金规则说明"}]
    assert get_result["document"] == {"title": "奖金规则说明"}
    assert calls == [
        ("list", "default", "default", None, 50),
        ("get", "default", "default", "奖金规则说明"),
    ]


def test_knowledge_admin_cli_calls_enable_and_disable():
    import asyncio

    from app.workers import knowledge_admin

    calls = []

    class FakeRepository:
        async def set_enabled(self, tenant_id: str, kb_scope: str, title: str, enabled: bool):
            calls.append((tenant_id, kb_scope, title, enabled))
            return {"updated": True, "rowcount": 1}

    disable_result = asyncio.run(
        knowledge_admin.run_command(
            knowledge_admin.build_arg_parser().parse_args(
                ["disable", "--tenant-id", "default", "--kb-scope", "default", "--title", "奖金规则说明"]
            ),
            FakeRepository(),
        )
    )
    enable_result = asyncio.run(
        knowledge_admin.run_command(
            knowledge_admin.build_arg_parser().parse_args(
                ["enable", "--tenant-id", "default", "--kb-scope", "default", "--title", "奖金规则说明"]
            ),
            FakeRepository(),
        )
    )

    assert disable_result["result"] == {"updated": True, "rowcount": 1}
    assert enable_result["result"] == {"updated": True, "rowcount": 1}
    assert calls == [
        ("default", "default", "奖金规则说明", False),
        ("default", "default", "奖金规则说明", True),
    ]
