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

    class FakePool:
        def close(self) -> None:
            calls["closed"] = True

        async def wait_closed(self) -> None:
            calls["wait_closed"] = True

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    async def fake_process_next_batch(pool, limit: int = 20):
        calls["limit"] = limit
        return [{"outbound_message": {"id": 1}}]

    monkeypatch.setattr(gateway_consumer, "Settings", FakeSettings)
    monkeypatch.setattr(gateway_consumer, "create_pool", fake_create_pool)
    monkeypatch.setattr(gateway_consumer, "process_next_batch", fake_process_next_batch)

    result = asyncio.run(gateway_consumer.run_once(limit=20))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-gateway",
        "livechat_account_id": "unused-for-gateway",
    }
    assert calls["limit"] == 20
    assert calls["closed"] is True
    assert calls["wait_closed"] is True
    assert result["processed"] == 1
    assert result["enqueued"] == 1


def test_sender_cli_parser_accepts_once_and_limit():
    from app.workers.sender_worker import build_arg_parser

    args = build_arg_parser().parse_args(["--once", "--limit", "20"])

    assert args.once is True
    assert args.limit == 20
