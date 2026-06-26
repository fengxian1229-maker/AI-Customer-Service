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

    async def fake_process_next_batch(pool, limit: int = 20, checkpoint_mode: str = "off"):
        calls["limit"] = limit
        calls["checkpoint_mode"] = checkpoint_mode
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
