import asyncio
import signal


class FakeSettings:
    livechat_agent_access_token = "livechat-token"
    livechat_account_id = "account-1"
    livechat_api_base = "https://livechat.example"
    livechat_allowed_group_ids = "23"
    livechat_self_author_ids = "agent-1"
    mysql_host = "127.0.0.1"
    mysql_user = "root"
    mysql_database = "ai_customer_service"
    poll_seconds = 0
    poll_limit = 20
    langgraph_checkpoint_mode = "memory"
    telegram_sop_enabled = True
    telegram_bot_token = "telegram-token"
    telegram_sop_target_chat_id = "-1001"
    telegram_finance_group = None
    telegram_test_group = None
    backend_query_enabled = True
    backend_provider_type = "mock"
    livechat_handoff_enabled = True
    livechat_handoff_target_group_id = 23

    @property
    def livechat_self_author_id_set(self):
        return {"agent-1"}


class FakePool:
    def __init__(self) -> None:
        self.close_calls = 0
        self.wait_closed_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


class FakeLiveChatClient:
    def __init__(self, *args) -> None:
        self.args = args


def patch_runner_runtime(monkeypatch, service_runner, tick_calls=None):
    pool = FakePool()
    created = {"pool_calls": 0, "pool": pool}

    async def fake_create_pool(settings):
        created["pool_calls"] += 1
        created["settings"] = settings
        return pool

    async def tick_factory(name):
        if tick_calls is not None:
            tick_calls[name] = tick_calls.get(name, 0) + 1
        return {"worker": name}

    monkeypatch.setattr(service_runner, "Settings", FakeSettings)
    monkeypatch.setattr(service_runner, "LiveChatSenderClient", FakeLiveChatClient)
    monkeypatch.setattr(service_runner, "create_pool", fake_create_pool)
    monkeypatch.setattr(service_runner, "polling_tick", lambda context: tick_factory("polling_receiver"))
    monkeypatch.setattr(service_runner, "gateway_tick", lambda context: tick_factory("gateway_consumer"))
    monkeypatch.setattr(service_runner, "sender_tick", lambda context: tick_factory("sender_worker"))
    monkeypatch.setattr(service_runner, "external_command_tick", lambda context: tick_factory("external_command_worker"))
    monkeypatch.setattr(service_runner, "external_result_tick", lambda context: tick_factory("external_result_consumer"))
    monkeypatch.setattr(service_runner, "telegram_reply_tick", lambda context: tick_factory("telegram_reply_consumer"))
    monkeypatch.setattr(service_runner, "livechat_idle_timer_tick", lambda context: tick_factory("livechat_idle_timer"))
    return created


def test_parser_supports_all():
    from app.workers.service_runner import build_arg_parser

    args = build_arg_parser().parse_args(["--all"])

    assert args.all is True


def test_parser_supports_shutdown_timeout_seconds():
    from app.workers.service_runner import build_arg_parser

    args = build_arg_parser().parse_args(["--all", "--shutdown-timeout-seconds", "2.5"])

    assert args.shutdown_timeout_seconds == 2.5


def test_no_all_returns_failed_usage(monkeypatch):
    from app.workers import service_runner

    class FailSettings:
        def __init__(self):
            raise AssertionError("Settings must not load for usage errors")

    monkeypatch.setattr(service_runner, "Settings", FailSettings)

    result = service_runner.run([])

    assert result["status"] == "FAILED_USAGE"


def test_negative_shutdown_timeout_returns_failed_usage(monkeypatch):
    from app.workers import service_runner

    class FailSettings:
        def __init__(self):
            raise AssertionError("Settings must not load for usage errors")

    monkeypatch.setattr(service_runner, "Settings", FailSettings)

    result = service_runner.run(["--all", "--shutdown-timeout-seconds", "-1"])

    assert result["status"] == "FAILED_USAGE"
    assert "--shutdown-timeout-seconds" in result["error"]


def test_preflight_missing_config_does_not_start_tasks(monkeypatch):
    from app.workers import service_runner

    class MissingSettings(FakeSettings):
        telegram_bot_token = None
        backend_provider_type = None
        livechat_handoff_target_group_id = None

    async def fail_create_pool(settings):
        raise AssertionError("preflight failure must not create a pool")

    monkeypatch.setattr(service_runner, "Settings", MissingSettings)
    monkeypatch.setattr(service_runner, "create_pool", fail_create_pool)

    result = service_runner.run(["--all", "--once"])

    assert result["status"] == "FAILED_PREFLIGHT"
    assert "TELEGRAM_BOT_TOKEN" in result["missing"]
    assert "BACKEND_PROVIDER_TYPE" in result["missing"]
    assert "LIVECHAT_HANDOFF_TARGET_GROUP_ID" in result["missing"]


def test_once_each_worker_executes_once(monkeypatch):
    from app.workers import service_runner

    tick_calls = {}
    created = patch_runner_runtime(monkeypatch, service_runner, tick_calls=tick_calls)

    result = service_runner.run(["--all", "--once"])

    assert result["status"] == "OK"
    assert tick_calls == {
        "polling_receiver": 1,
        "gateway_consumer": 1,
        "sender_worker": 1,
        "external_command_worker": 1,
        "external_result_consumer": 1,
        "telegram_reply_consumer": 1,
        "livechat_idle_timer": 1,
    }
    assert created["pool_calls"] == 1
    assert created["pool"].close_calls == 1
    assert created["pool"].wait_closed_calls == 1


def test_max_iterations_two_each_worker_executes_twice(monkeypatch):
    from app.workers import service_runner

    tick_calls = {}
    patch_runner_runtime(monkeypatch, service_runner, tick_calls=tick_calls)

    result = service_runner.run([
        "--all",
        "--max-iterations",
        "2",
        "--gateway-seconds",
        "0",
        "--sender-seconds",
        "0",
        "--external-command-seconds",
        "0",
        "--external-result-seconds",
        "0",
        "--telegram-reply-seconds",
        "0",
        "--livechat-idle-timer-seconds",
        "0",
    ])

    assert result["status"] == "OK"
    assert set(tick_calls.values()) == {2}
    assert set(tick_calls) == set(service_runner.WORKER_NAMES)


def test_workers_run_independently_without_waiting_for_slow_polling(monkeypatch):
    from app.workers import service_runner

    polling_started = asyncio.Event()
    polling_done = asyncio.Event()
    gateway_started_before_polling_done = {"value": False}
    patch_runner_runtime(monkeypatch, service_runner)

    async def slow_polling(context):
        polling_started.set()
        await asyncio.sleep(0.05)
        polling_done.set()
        return {"worker": "polling_receiver"}

    async def fast_gateway(context):
        await polling_started.wait()
        gateway_started_before_polling_done["value"] = not polling_done.is_set()
        return {"worker": "gateway_consumer"}

    monkeypatch.setattr(service_runner, "polling_tick", slow_polling)
    monkeypatch.setattr(service_runner, "gateway_tick", fast_gateway)

    result = service_runner.run(["--all", "--once"])

    assert result["status"] == "OK"
    assert gateway_started_before_polling_done["value"] is True


def test_continue_on_error_default_keeps_worker_running(monkeypatch):
    from app.workers import service_runner

    patch_runner_runtime(monkeypatch, service_runner)
    calls = {"sender": 0}

    async def flaky_sender(context):
        calls["sender"] += 1
        if calls["sender"] == 1:
            raise RuntimeError("sender failed")
        return {"worker": "sender_worker"}

    monkeypatch.setattr(service_runner, "sender_tick", flaky_sender)

    result = service_runner.run([
        "--all",
        "--max-iterations",
        "2",
        "--gateway-seconds",
        "0",
        "--sender-seconds",
        "0",
        "--external-command-seconds",
        "0",
        "--external-result-seconds",
        "0",
        "--telegram-reply-seconds",
        "0",
        "--livechat-idle-timer-seconds",
        "0",
    ])

    assert result["status"] == "OK"
    assert calls["sender"] == 2
    assert result["workers"]["sender_worker"]["errors"] == 1
    assert result["errors"]


def test_stop_on_error_stops_runner(monkeypatch):
    from app.workers import service_runner

    patch_runner_runtime(monkeypatch, service_runner)

    async def failing_sender(context):
        raise RuntimeError("sender failed")

    monkeypatch.setattr(service_runner, "sender_tick", failing_sender)

    result = service_runner.run(["--all", "--stop-on-error"])

    assert result["status"] == "STOPPED_ON_ERROR"
    assert result["shutdown_reason"] == "stop_on_error"
    assert result["errors"][0]["worker"] == "sender_worker"


def test_install_signal_handlers_registers_sigterm_and_sigint():
    from app.workers import service_runner

    class FakeLoop:
        def __init__(self):
            self.calls = []

        def add_signal_handler(self, sig, callback, *args):
            self.calls.append((sig, callback, args))

    loop = FakeLoop()
    stop_event = asyncio.Event()
    shutdown_reason = {"value": None}

    registered = service_runner.install_signal_handlers(loop, stop_event, shutdown_reason)

    assert registered == [signal.SIGTERM, signal.SIGINT]
    assert [call[0] for call in loop.calls] == [signal.SIGTERM, signal.SIGINT]


def test_signal_handler_sets_stop_event_and_shutdown_reason():
    from app.workers import service_runner

    stop_event = asyncio.Event()
    shutdown_reason = {"value": None}

    service_runner._request_shutdown(stop_event, shutdown_reason, "signal:SIGTERM")

    assert stop_event.is_set() is True
    assert shutdown_reason["value"] == "signal:SIGTERM"


def test_run_all_workers_exits_gracefully_after_signal(monkeypatch):
    from app.workers import service_runner

    context = make_context(service_runner)
    callbacks = {}
    removed = {"called": False}
    calls = {"polling": 0}

    def fake_install(loop, stop_event, shutdown_reason):
        callbacks["sigterm"] = lambda: service_runner._request_shutdown(stop_event, shutdown_reason, "signal:SIGTERM")
        return [signal.SIGTERM]

    def fake_remove(loop, registered_signals):
        removed["called"] = True
        removed["registered"] = registered_signals

    async def signal_polling(context):
        calls["polling"] += 1
        callbacks["sigterm"]()
        return {"worker": "polling_receiver"}

    async def fast_tick(context):
        return {"worker": "ok"}

    monkeypatch.setattr(service_runner, "install_signal_handlers", fake_install)
    monkeypatch.setattr(service_runner, "remove_signal_handlers", fake_remove)
    monkeypatch.setattr(service_runner, "polling_tick", signal_polling)
    monkeypatch.setattr(service_runner, "gateway_tick", fast_tick)
    monkeypatch.setattr(service_runner, "sender_tick", fast_tick)
    monkeypatch.setattr(service_runner, "external_command_tick", fast_tick)
    monkeypatch.setattr(service_runner, "external_result_tick", fast_tick)
    monkeypatch.setattr(service_runner, "telegram_reply_tick", fast_tick)
    monkeypatch.setattr(service_runner, "livechat_idle_timer_tick", fast_tick)

    result = asyncio.run(service_runner.run_all_workers(context))

    assert result["status"] == "CANCELLED"
    assert result["shutdown_reason"] == "signal:SIGTERM"
    assert calls["polling"] == 1
    assert removed == {"called": True, "registered": [signal.SIGTERM]}


def test_shutdown_timeout_cancels_pending_worker(monkeypatch):
    from app.workers import service_runner

    context = make_context(service_runner, shutdown_timeout_seconds=0.01)
    callbacks = {}

    def fake_install(loop, stop_event, shutdown_reason):
        callbacks["sigint"] = lambda: service_runner._request_shutdown(stop_event, shutdown_reason, "signal:SIGINT")
        return [signal.SIGINT]

    async def stuck_polling(context):
        callbacks["sigint"]()
        await asyncio.Event().wait()
        return {"worker": "polling_receiver"}

    async def fast_tick(context):
        return {"worker": "ok"}

    monkeypatch.setattr(service_runner, "install_signal_handlers", fake_install)
    monkeypatch.setattr(service_runner, "polling_tick", stuck_polling)
    monkeypatch.setattr(service_runner, "gateway_tick", fast_tick)
    monkeypatch.setattr(service_runner, "sender_tick", fast_tick)
    monkeypatch.setattr(service_runner, "external_command_tick", fast_tick)
    monkeypatch.setattr(service_runner, "external_result_tick", fast_tick)
    monkeypatch.setattr(service_runner, "telegram_reply_tick", fast_tick)

    result = asyncio.run(service_runner.run_all_workers(context))

    assert result["status"] == "SHUTDOWN_TIMEOUT"
    assert result["shutdown_reason"] == "signal:SIGINT"


def test_service_runner_does_not_call_worker_run_once(monkeypatch):
    from app.workers import service_runner

    patch_runner_runtime(monkeypatch, service_runner)

    async def fail_run_once(*args, **kwargs):
        raise AssertionError("service_runner must not call worker run_once")

    monkeypatch.setattr(service_runner.polling_receiver, "run_once", fail_run_once)
    monkeypatch.setattr(service_runner.gateway_consumer, "run_once", fail_run_once)
    monkeypatch.setattr(service_runner.sender_worker, "run_once", fail_run_once)
    monkeypatch.setattr(service_runner.external_command_worker, "run_once", fail_run_once)
    monkeypatch.setattr(service_runner.external_result_consumer, "run_once", fail_run_once)
    monkeypatch.setattr(service_runner.telegram_reply_consumer, "run_once", fail_run_once)

    result = service_runner.run(["--all", "--once"])

    assert result["status"] == "OK"


def test_gateway_tick_uses_shared_pool_settings_and_checkpoint_mode(monkeypatch):
    from app.workers import service_runner

    calls = {}
    context = make_context(service_runner, gateway_limit=7)

    async def fake_process_next_batch(pool, limit, checkpoint_mode, settings):
        calls.update({"pool": pool, "limit": limit, "checkpoint_mode": checkpoint_mode, "settings": settings})
        return {"processed": 1, "failed": 0, "enqueued": 1, "failures": [], "llm": {"provider": "mock"}}

    monkeypatch.setattr(service_runner.gateway_consumer, "process_next_batch", fake_process_next_batch)

    result = asyncio.run(service_runner.gateway_tick(context))

    assert calls["pool"] is context.pool
    assert calls["settings"] is context.settings
    assert calls["checkpoint_mode"] == context.settings.langgraph_checkpoint_mode
    assert calls["limit"] == 7
    assert result["processed"] == 1


def test_sender_tick_uses_sender_worker_process_next_batch(monkeypatch):
    from app.workers import service_runner

    calls = {}
    context = make_context(service_runner, sender_limit=9)

    async def fake_process_next_batch(pool, sender_client, limit):
        calls.update({"pool": pool, "sender_client": sender_client, "limit": limit})
        return [{"status": "SENT"}, {"status": "RETRYABLE"}]

    monkeypatch.setattr(service_runner.sender_worker, "process_next_batch", fake_process_next_batch)

    result = asyncio.run(service_runner.sender_tick(context))

    assert calls["pool"] is context.pool
    assert calls["sender_client"] is context.sender_client
    assert calls["limit"] == 9
    assert result["processed"] == 2
    assert result["sent"] == 1
    assert result["retryable"] == 1


def test_external_command_tick_passes_real_execution_flags(monkeypatch):
    from app.workers import service_runner

    calls = {}
    context = make_context(service_runner, external_command_limit=11)

    async def fake_process_pending_commands(*args, **kwargs):
        calls.update(kwargs)
        return [{"status": "SENT", "result_insert": {"inserted": True}}]

    monkeypatch.setattr(service_runner.external_command_worker, "process_pending_commands", fake_process_pending_commands)

    result = asyncio.run(service_runner.external_command_tick(context))

    assert calls["limit"] == 11
    assert calls["dry_run"] is False
    assert calls["emit_result"] is True
    assert calls["execute_human_handoff"] is True
    assert calls["execute_telegram"] is True
    assert calls["execute_backend"] is True
    assert calls["settings"] is context.settings
    assert result["emitted_result"] == 1


def test_telegram_reply_tick_uses_reusable_process_function(monkeypatch):
    from app.workers import service_runner

    calls = {}
    context = make_context(service_runner, telegram_reply_limit=13)

    async def fake_process_telegram_updates_once(**kwargs):
        calls.update(kwargs)
        return {"worker": "telegram_reply_consumer", "updates": 0, "recorded": 0, "duplicates": 0, "ignored": 0, "failed": 0}

    async def fail_run_once(*args, **kwargs):
        raise AssertionError("service_runner must not call telegram_reply_consumer.run_once")

    monkeypatch.setattr(service_runner.telegram_reply_consumer, "process_telegram_updates_once", fake_process_telegram_updates_once)
    monkeypatch.setattr(service_runner.telegram_reply_consumer, "run_once", fail_run_once)

    result = asyncio.run(service_runner.telegram_reply_tick(context))

    assert calls["pool"] is context.pool
    assert calls["settings"] is context.settings
    assert calls["limit"] == 13
    assert calls["timeout"] == 0
    assert result["worker"] == "telegram_reply_consumer"


def test_pool_created_once_and_closed_once(monkeypatch):
    from app.workers import service_runner

    created = patch_runner_runtime(monkeypatch, service_runner)

    result = service_runner.run(["--all", "--once"])

    assert result["status"] == "OK"
    assert created["pool_calls"] == 1
    assert created["pool"].close_calls == 1
    assert created["pool"].wait_closed_calls == 1


def make_context(
    service_runner,
    poll_limit=20,
    gateway_limit=20,
    sender_limit=20,
    external_command_limit=20,
    external_result_limit=20,
    telegram_reply_limit=20,
    livechat_idle_timer_limit=20,
    shutdown_timeout_seconds=30.0,
):
    config = service_runner.ServiceRunnerConfig(
        poll_seconds=0,
        gateway_seconds=0,
        sender_seconds=0,
        external_command_seconds=0,
        external_result_seconds=0,
        telegram_reply_seconds=0,
        livechat_idle_timer_seconds=0,
        poll_limit=poll_limit,
        gateway_limit=gateway_limit,
        sender_limit=sender_limit,
        external_command_limit=external_command_limit,
        external_result_limit=external_result_limit,
        telegram_reply_limit=telegram_reply_limit,
        livechat_idle_timer_limit=livechat_idle_timer_limit,
        max_iterations=1,
        stop_on_error=False,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )
    return service_runner.ServiceRunnerContext(
        settings=FakeSettings(),
        pool=FakePool(),
        polling_client=FakeLiveChatClient(),
        sender_client=FakeLiveChatClient(),
        groups={23},
        config=config,
    )
