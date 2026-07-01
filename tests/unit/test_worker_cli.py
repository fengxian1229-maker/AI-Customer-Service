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
                        },
                        "llm_router": {
                            "provider": "mock",
                            "status": "fallback",
                            "fallback_reason": "low_confidence",
                            "api_key": "hidden",
                        },
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
    assert result["router_count"] == 1
    assert result["router_fallback_count"] == 1
    assert result["latest"]["llm_shadow"]["rewrite"] == {"provider": "mock", "status": "ok"}
    assert result["latest"]["llm_router"] == {
        "provider": "mock",
        "status": "fallback",
        "fallback_reason": "low_confidence",
    }
    assert "api_key" not in str(result)
    assert calls["closed"] is True
    assert calls["wait_closed"] is True


def test_llm_shadow_admin_sanitizes_datetime_and_json_dumps():
    import json
    from datetime import datetime

    from app.workers.llm_shadow_admin import _sanitize_shadow

    result = _sanitize_shadow(
        {
            "created_at": datetime(2026, 6, 27, 1, 2, 3),
            "router": {"status": "fallback", "api_key": "hidden", "error_message": "boom"},
            "password": "hidden",
        }
    )

    dumped = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    assert "2026-06-27T01:02:03" in dumped
    assert "api_key" not in dumped
    assert "password" not in dumped


def test_real_gemini_faq_smoke_parser_defaults():
    from app.workers.real_gemini_faq_smoke import build_arg_parser

    args = build_arg_parser().parse_args([])

    assert args.text == "怎么存款？"
    assert args.tenant_id == "default"
    assert args.kb_scope == "default"
    assert args.send is False
    assert args.sender_limit == 10
    assert args.mark_unsent_smoke_skipped is True


def test_real_gemini_faq_smoke_rejects_wrong_settings_before_writes(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    class FakeSettings:
        llm_provider = "mock"
        llm_router_mode = "faq_authoritative"

    monkeypatch.setattr(real_gemini_faq_smoke, "Settings", lambda: FakeSettings())

    result = asyncio.run(real_gemini_faq_smoke.run(["--text", "怎么存款？"]))

    assert result["worker"] == "real_gemini_faq_smoke"
    assert result["smoke_success"] is False
    assert result["error"]["code"] == "invalid_settings"
    assert "llm_provider=gemini" in result["error"]["message"]
    assert result["inbound_event_id"] is None


def test_real_gemini_faq_smoke_no_send_uses_unused_livechat_credentials(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    calls = {}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs
            self.llm_provider = "Gemini"
            self.llm_router_mode = "FAQ_AUTHORITATIVE"
            self.langgraph_checkpoint_mode = "off"

    class FakePool:
        def close(self) -> None:
            calls["closed"] = True

        async def wait_closed(self) -> None:
            calls["wait_closed"] = True

    async def fake_create_pool(settings):
        calls["settings"] = settings
        return FakePool()

    async def fake_insert_smoke_event(pool, args, summary):
        return 55

    async def fake_process_inbound_event_id(pool, inbound_event_id, checkpoint_mode="off", settings=None):
        return {"processed": 1, "failed": 0, "enqueued": 1, "inbound_event_id": inbound_event_id, "failures": []}

    async def fake_fetch_latest_router_metadata(pool, conversation_id, inbound_event_id):
        return {"status": "accepted", "final_route": "faq", "mode": "faq_authoritative"}

    async def fake_fetch_outbound_messages(pool, conversation_id, inbound_event_id):
        return [{"id": 7, "inbound_event_id": inbound_event_id, "status": "PENDING"}]

    async def fake_fetch_graph_run_errors(pool, conversation_id, inbound_event_id):
        return []

    class FakeOutboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            calls["skipped_inbound_event_id"] = inbound_event_id
            return 1

    monkeypatch.setattr(real_gemini_faq_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_faq_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(real_gemini_faq_smoke, "_insert_smoke_event", fake_insert_smoke_event)
    monkeypatch.setattr(real_gemini_faq_smoke.gateway_consumer, "process_inbound_event_id", fake_process_inbound_event_id)
    monkeypatch.setattr(real_gemini_faq_smoke, "_fetch_latest_router_metadata", fake_fetch_latest_router_metadata)
    monkeypatch.setattr(real_gemini_faq_smoke, "_fetch_outbound_messages", fake_fetch_outbound_messages)
    monkeypatch.setattr(real_gemini_faq_smoke, "_fetch_graph_run_errors", fake_fetch_graph_run_errors)
    monkeypatch.setattr(real_gemini_faq_smoke, "OutboundMessageRepository", FakeOutboundRepository)

    result = asyncio.run(real_gemini_faq_smoke.run(["--text", "怎么存款？"]))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-real-gemini-faq-smoke",
        "livechat_account_id": "unused-for-real-gemini-faq-smoke",
    }
    assert result["smoke_success"] is True
    assert result["skipped_unsent_count"] == 1
    assert calls["skipped_inbound_event_id"] == 55


def test_real_gemini_faq_smoke_send_requires_explicit_chat_and_thread(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "faq_authoritative"

    monkeypatch.setattr(real_gemini_faq_smoke, "Settings", FakeSettings)

    missing_chat = asyncio.run(real_gemini_faq_smoke.run(["--send", "--thread-id", "thread-1"]))
    missing_thread = asyncio.run(real_gemini_faq_smoke.run(["--send", "--chat-id", "chat-1"]))

    assert missing_chat["error"]["code"] == "send_requires_explicit_chat_id"
    assert missing_chat["inbound_event_id"] is None
    assert missing_thread["error"]["code"] == "send_requires_explicit_thread_id"
    assert missing_thread["inbound_event_id"] is None


def test_real_gemini_faq_smoke_send_fails_when_sender_limit_leaves_pending(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING", "PENDING", "PENDING"],
            sender_results=[{"status": "SENT", "inbound_event_id": 55}],
            after_statuses=["SENT", "PENDING", "PENDING"],
        )
    )

    assert result["smoke_success"] is False
    assert result["pending_before_count"] == 3
    assert result["sender_result_count"] == 1
    assert result["pending_after_count"] == 2
    assert "sender_limit_may_have_left_pending_outbounds" in result["warning"]


def test_real_gemini_faq_smoke_send_succeeds_when_all_pending_are_sent(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING", "PENDING"],
            sender_results=[
                {"status": "SENT", "inbound_event_id": 55},
                {"status": "SENT", "inbound_event_id": 55},
            ],
            after_statuses=["SENT", "SENT"],
        )
    )

    assert result["smoke_success"] is True
    assert result["pending_before_count"] == 2
    assert result["sender_result_count"] == 2
    assert result["pending_after_count"] == 0


def test_real_gemini_faq_smoke_send_fails_for_failed_sender_status(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING"],
            sender_results=[{"status": "FAILED_UNKNOWN", "inbound_event_id": 55}],
            after_statuses=["FAILED_UNKNOWN"],
        )
    )

    assert result["smoke_success"] is False
    assert "sender_results_not_all_send_safe" in result["warning"]


def test_real_gemini_faq_smoke_send_allows_buttons_preview_with_warning(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING", "PENDING"],
            sender_results=[
                {"status": "SENT", "inbound_event_id": 55},
                {"status": "SKIPPED_PREVIEW", "inbound_event_id": 55},
            ],
            after_statuses=["SENT", "SKIPPED_PREVIEW"],
        )
    )

    assert result["smoke_success"] is True
    assert result["warning"] == "buttons preview was skipped by sender_worker"


def test_real_gemini_faq_smoke_send_fails_for_mismatched_sender_inbound_event(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING"],
            sender_results=[{"status": "SENT", "inbound_event_id": 99}],
            after_statuses=["SENT"],
        )
    )

    assert result["smoke_success"] is False
    assert "sender_results_not_scoped_to_inbound_event" in result["warning"]


def test_real_gemini_faq_smoke_send_blocks_before_sender_when_router_fallback(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result, calls = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING"],
            sender_results=[{"status": "SENT", "inbound_event_id": 55}],
            after_statuses=["SKIPPED_MANUAL_SMOKE"],
            llm_router={"status": "fallback", "final_route": "clarification", "mode": "faq_authoritative"},
            return_calls=True,
        )
    )

    assert calls["sender_called"] == 0
    assert calls["skipped_inbound_event_ids"] == [55]
    assert result["send_blocked"] is True
    assert result["send_block_reason"] == "llm_router_not_accepted_faq"
    assert result["sender_results"] == []
    assert result["smoke_success"] is False
    assert "send blocked before LiveChat dispatch" in result["warning"]


def test_real_gemini_faq_smoke_send_blocks_when_final_route_is_not_faq(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result, calls = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING"],
            sender_results=[{"status": "SENT", "inbound_event_id": 55}],
            after_statuses=["SKIPPED_MANUAL_SMOKE"],
            llm_router={"status": "accepted", "final_route": "clarification", "mode": "faq_authoritative"},
            return_calls=True,
        )
    )

    assert calls["sender_called"] == 0
    assert result["send_blocked"] is True
    assert result["send_block_reason"] == "llm_router_not_accepted_faq"
    assert result["smoke_success"] is False


def test_real_gemini_faq_smoke_send_blocks_when_graph_run_errors_exist(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result, calls = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING"],
            sender_results=[{"status": "SENT", "inbound_event_id": 55}],
            after_statuses=["SKIPPED_MANUAL_SMOKE"],
            graph_run_errors=[{"id": 1, "inbound_event_id": 55, "error_type": "RuntimeError"}],
            return_calls=True,
        )
    )

    assert calls["sender_called"] == 0
    assert result["send_blocked"] is True
    assert result["send_block_reason"] == "graph_run_errors_present"
    assert result["smoke_success"] is False


def test_real_gemini_faq_smoke_send_calls_sender_only_after_accepted_faq_gate(monkeypatch):
    import asyncio

    from app.workers import real_gemini_faq_smoke

    result, calls = asyncio.run(
        _run_faq_smoke_send_case(
            monkeypatch,
            real_gemini_faq_smoke,
            before_statuses=["PENDING"],
            sender_results=[{"status": "SENT", "inbound_event_id": 55}],
            after_statuses=["SENT"],
            return_calls=True,
        )
    )

    assert calls["sender_called"] == 1
    assert calls["skipped_inbound_event_ids"] == []
    assert result["send_blocked"] is False
    assert result["smoke_success"] is True


async def _run_faq_smoke_send_case(
    monkeypatch,
    real_gemini_faq_smoke,
    before_statuses,
    sender_results,
    after_statuses,
    llm_router=None,
    graph_run_errors=None,
    return_calls=False,
):
    calls = {"fetch_outbound": 0, "sender_called": 0, "skipped_inbound_event_ids": []}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "faq_authoritative"
            self.langgraph_checkpoint_mode = "off"
            self.livechat_api_base = "https://example.test"
            self.livechat_account_id = "account"
            self.livechat_agent_access_token = "token"

    class FakePool:
        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def fake_create_pool(settings):
        return FakePool()

    async def fake_insert_smoke_event(pool, args, summary):
        return 55

    async def fake_process_inbound_event_id(pool, inbound_event_id, checkpoint_mode="off", settings=None):
        return {"processed": 1, "failed": 0, "enqueued": 1, "inbound_event_id": inbound_event_id, "failures": []}

    async def fake_fetch_latest_router_metadata(pool, conversation_id, inbound_event_id):
        return llm_router or {"status": "accepted", "final_route": "faq", "mode": "faq_authoritative"}

    async def fake_fetch_outbound_messages(pool, conversation_id, inbound_event_id):
        calls["fetch_outbound"] += 1
        statuses = before_statuses if calls["fetch_outbound"] == 1 else after_statuses
        return [
            {"id": index + 1, "inbound_event_id": inbound_event_id, "status": status}
            for index, status in enumerate(statuses)
        ]

    async def fake_fetch_graph_run_errors(pool, conversation_id, inbound_event_id):
        return graph_run_errors or []

    async def fake_process_pending_for_inbound_event(pool, sender_client, inbound_event_id, limit=20):
        calls["sender_called"] += 1
        return sender_results

    class FakeOutboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            calls["skipped_inbound_event_ids"].append(inbound_event_id)
            return sum(1 for status in before_statuses if status == "PENDING")

    monkeypatch.setattr(real_gemini_faq_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_faq_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(real_gemini_faq_smoke, "_insert_smoke_event", fake_insert_smoke_event)
    monkeypatch.setattr(real_gemini_faq_smoke.gateway_consumer, "process_inbound_event_id", fake_process_inbound_event_id)
    monkeypatch.setattr(real_gemini_faq_smoke, "_fetch_latest_router_metadata", fake_fetch_latest_router_metadata)
    monkeypatch.setattr(real_gemini_faq_smoke, "_fetch_outbound_messages", fake_fetch_outbound_messages)
    monkeypatch.setattr(real_gemini_faq_smoke, "_fetch_graph_run_errors", fake_fetch_graph_run_errors)
    monkeypatch.setattr(real_gemini_faq_smoke.sender_worker, "process_pending_for_inbound_event", fake_process_pending_for_inbound_event)
    monkeypatch.setattr(real_gemini_faq_smoke, "OutboundMessageRepository", FakeOutboundRepository)

    result = await real_gemini_faq_smoke.run(["--send", "--chat-id", "chat-1", "--thread-id", "thread-1", "--sender-limit", "1"])
    return (result, calls) if return_calls else result


def test_real_gemini_faq_smoke_router_and_errors_queries_filter_inbound_event_id():
    import asyncio
    import json

    from app.workers import real_gemini_faq_smoke

    class FakeCursor:
        def __init__(self) -> None:
            self.queries = []

        async def execute(self, sql, args):
            self.queries.append((sql, args))

        async def fetchall(self):
            sql, args = self.queries[-1]
            if "graph_checkpoint_runs" in sql:
                return [{"metadata_json": json.dumps({"llm_router": {"status": "accepted", "inbound": args[1]}})}]
            if "graph_run_errors" in sql:
                return [{"id": 1, "inbound_event_id": args[1], "error_type": "RuntimeError", "error_message": "boom"}]
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def __init__(self, cursor) -> None:
            self.cursor_obj = cursor

        def cursor(self, *args, **kwargs):
            return self.cursor_obj

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self) -> None:
            self.cursor = FakeCursor()

        def acquire(self):
            return FakeConnection(self.cursor)

    pool = FakePool()

    router = asyncio.run(real_gemini_faq_smoke._fetch_latest_router_metadata(pool, "livechat:chat-1", 55))
    errors = asyncio.run(real_gemini_faq_smoke._fetch_graph_run_errors(pool, "livechat:chat-1", 55))

    assert router == {"status": "accepted", "inbound": 55}
    assert errors == [{"id": 1, "inbound_event_id": 55, "error_type": "RuntimeError", "error_message": "boom"}]
    checkpoint_sql, checkpoint_args = pool.cursor.queries[0]
    error_sql, error_args = pool.cursor.queries[1]
    assert "AND inbound_event_id = %s" in checkpoint_sql
    assert checkpoint_args == ("livechat:chat-1", 55)
    assert "AND inbound_event_id = %s" in error_sql
    assert error_args == ("livechat:chat-1", 55)


def test_real_gemini_guarded_smoke_parser_defaults():
    from app.workers.real_gemini_guarded_smoke import build_arg_parser

    args = build_arg_parser().parse_args([])

    assert args.case_set == "default"
    assert args.tenant_id == "default"
    assert args.kb_scope == "default"
    assert args.seed_default_faq is False
    assert args.limit is None
    assert args.case is None


def test_real_gemini_guarded_smoke_rejects_invalid_settings_before_writes(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "mock"
            self.llm_router_mode = "guarded_authoritative"

    async def fail_create_pool(settings):
        raise AssertionError("invalid settings must not create a pool")

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fail_create_pool)

    result = asyncio.run(real_gemini_guarded_smoke.run([]))

    assert result["worker"] == "real_gemini_guarded_smoke"
    assert result["error"]["code"] == "invalid_settings"
    assert result["total"] == 0
    assert result["smoke_success"] is False


def test_real_gemini_guarded_smoke_runs_cases_with_unique_fake_threads_and_scoped_processing(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    calls = {"insert_summaries": [], "processed": [], "skipped": []}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs
            self.llm_provider = "Gemini"
            self.llm_router_mode = "GUARDED_AUTHORITATIVE"
            self.llm_router_min_confidence = 0.75
            self.langgraph_checkpoint_mode = "off"

    class FakePool:
        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeOutboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            calls["skipped"].append(inbound_event_id)
            return 1

    class FakeExternalCommandRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            return 0

    async def fake_create_pool(settings):
        return FakePool()

    async def fake_insert_smoke_event(pool, case, summary):
        calls["insert_summaries"].append(summary)
        return 100 + len(calls["insert_summaries"])

    async def fake_process_inbound_event_id(pool, inbound_event_id, checkpoint_mode="off", settings=None):
        calls["processed"].append(inbound_event_id)
        return {"processed": 1, "failed": 0, "enqueued": 1, "inbound_event_id": inbound_event_id, "results": [{"graph_state": {"route": "faq"}}], "failures": []}

    async def fake_fetch_latest_router_metadata(pool, conversation_id, inbound_event_id):
        return {"status": "accepted", "final_route": "faq", "route_source": "llm_guarded_authoritative"}

    async def fake_fetch_outbound_messages(pool, conversation_id, inbound_event_id):
        return [{"id": 1, "inbound_event_id": inbound_event_id, "status": "PENDING"}]

    async def fake_fetch_external_commands(pool, conversation_id, inbound_event_id):
        return []

    async def fake_fetch_graph_run_errors(pool, conversation_id, inbound_event_id):
        return []

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_insert_smoke_event", fake_insert_smoke_event)
    monkeypatch.setattr(real_gemini_guarded_smoke.gateway_consumer, "process_inbound_event_id", fake_process_inbound_event_id)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_latest_router_metadata", fake_fetch_latest_router_metadata)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_outbound_messages", fake_fetch_outbound_messages)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_external_commands", fake_fetch_external_commands)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_graph_run_errors", fake_fetch_graph_run_errors)
    monkeypatch.setattr(real_gemini_guarded_smoke, "OutboundMessageRepository", FakeOutboundRepository)
    monkeypatch.setattr(real_gemini_guarded_smoke, "ExternalCommandRepository", FakeExternalCommandRepository)

    result = asyncio.run(real_gemini_guarded_smoke.run(["--case", "faq_deposit_howto_zh"]))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-real-gemini-guarded-smoke",
        "livechat_account_id": "unused-for-real-gemini-guarded-smoke",
    }
    assert calls["processed"] == [101]
    assert calls["skipped"] == [101]
    assert len(calls["insert_summaries"]) == 1
    assert calls["insert_summaries"][0]["chat_id"].startswith("manual-gemini-guarded-faq_deposit_howto_zh-")
    assert result["cases"][0]["pass"] is True
    assert result["smoke_success"] is True


def test_real_gemini_guarded_smoke_evaluator_blocks_non_faq_safety_cases():
    from app.workers.real_gemini_guarded_smoke import DEFAULT_CASES, evaluate_case_result

    cases = {case["case_id"]: case for case in DEFAULT_CASES}

    assert evaluate_case_result(cases["faq_deposit_howto_zh"], "faq", "llm_guarded_authoritative", None, [])["pass"] is True
    assert evaluate_case_result(cases["sop_deposit_missing_es"], "faq", "llm_guarded_authoritative", None, [])["pass"] is False
    assert evaluate_case_result(cases["explicit_human_en"], "faq", "llm_guarded_authoritative", None, [])["pass"] is False
    assert evaluate_case_result(cases["backend_fact_balance_en"], "faq", "llm_guarded_authoritative", None, [])["pass"] is False
    assert evaluate_case_result(cases["file_without_text"], "faq", "llm_guarded_authoritative", {"status": "accepted"}, [])["pass"] is False


def test_real_gemini_guarded_smoke_evaluator_allows_declared_external_commands():
    from app.workers.real_gemini_guarded_smoke import DEFAULT_CASES, evaluate_case_result

    cases = {case["case_id"]: case for case in DEFAULT_CASES}

    explicit_human = evaluate_case_result(
        cases["explicit_human_en"],
        "human_handoff",
        "deterministic",
        None,
        [{"command_type": "human_handoff.requested"}],
    )
    backend_fact = evaluate_case_result(
        cases["backend_fact_balance_en"],
        "human_handoff",
        "deterministic",
        None,
        [{"command_type": "human_handoff.requested"}],
    )
    faq_with_command = evaluate_case_result(
        cases["faq_deposit_howto_zh"],
        "faq",
        "llm_guarded_authoritative",
        None,
        [{"command_type": "human_handoff.requested"}],
    )
    bad_type = evaluate_case_result(
        cases["explicit_human_en"],
        "human_handoff",
        "deterministic",
        None,
        [{"command_type": "telegram.send_case_card"}],
    )

    assert explicit_human["pass"] is True
    assert backend_fact["pass"] is True
    assert faq_with_command == {"pass": False, "failure_reason": "external_command_count_mismatch"}
    assert bad_type == {
        "pass": False,
        "failure_reason": "external_command_type_not_allowed:telegram.send_case_card",
    }


def test_real_gemini_guarded_smoke_failure_sets_summary_failure(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "guarded_authoritative"
            self.llm_router_min_confidence = 0.75
            self.langgraph_checkpoint_mode = "off"

    class FakePool:
        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def fake_create_pool(settings):
        return FakePool()

    async def fake_insert_smoke_event(pool, case, summary):
        return 55

    async def fake_process_inbound_event_id(pool, inbound_event_id, checkpoint_mode="off", settings=None):
        return {"processed": 1, "failed": 0, "inbound_event_id": inbound_event_id, "results": [{"graph_state": {"route": "faq"}}], "failures": []}

    async def fake_fetch_latest_router_metadata(pool, conversation_id, inbound_event_id):
        return {"status": "accepted", "final_route": "faq", "route_source": "llm_guarded_authoritative"}

    async def fake_empty(*args, **kwargs):
        return []

    class FakeOutboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            return 0

    class FakeExternalCommandRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            return 0

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_insert_smoke_event", fake_insert_smoke_event)
    monkeypatch.setattr(real_gemini_guarded_smoke.gateway_consumer, "process_inbound_event_id", fake_process_inbound_event_id)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_latest_router_metadata", fake_fetch_latest_router_metadata)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_outbound_messages", fake_empty)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_external_commands", fake_empty)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_graph_run_errors", fake_empty)
    monkeypatch.setattr(real_gemini_guarded_smoke, "OutboundMessageRepository", FakeOutboundRepository)
    monkeypatch.setattr(real_gemini_guarded_smoke, "ExternalCommandRepository", FakeExternalCommandRepository)

    result = asyncio.run(real_gemini_guarded_smoke.run(["--case", "sop_deposit_missing_es"]))

    assert result["failed"] == 1
    assert result["smoke_success"] is False


def test_real_gemini_guarded_smoke_skips_outbound_and_external_commands_for_case(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    calls = {"outbound_skipped": [], "external_skipped": []}

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "guarded_authoritative"
            self.llm_router_min_confidence = 0.75
            self.langgraph_checkpoint_mode = "off"

    class FakePool:
        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeOutboundRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            calls["outbound_skipped"].append((inbound_event_id, error))
            return 1

    class FakeExternalCommandRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def mark_pending_by_inbound_event_skipped(self, inbound_event_id: int, error: str):
            calls["external_skipped"].append((inbound_event_id, error))
            return 2

    async def fake_create_pool(settings):
        return FakePool()

    async def fake_insert_smoke_event(pool, case, summary):
        return 55

    async def fake_process_inbound_event_id(pool, inbound_event_id, checkpoint_mode="off", settings=None):
        return {"processed": 1, "failed": 0, "inbound_event_id": inbound_event_id, "results": [{"graph_state": {"route": "human_handoff"}}], "failures": []}

    async def fake_fetch_latest_router_metadata(pool, conversation_id, inbound_event_id):
        return {"status": "fallback", "final_route": "human_handoff", "route_source": "deterministic"}

    async def fake_fetch_outbound_messages(pool, conversation_id, inbound_event_id):
        return [{"id": 1, "inbound_event_id": inbound_event_id, "status": "PENDING"}]

    async def fake_fetch_external_commands(pool, conversation_id, inbound_event_id):
        return [{"id": 7, "inbound_event_id": inbound_event_id, "status": "PENDING", "command_type": "human_handoff.requested"}]

    async def fake_fetch_graph_run_errors(pool, conversation_id, inbound_event_id):
        return []

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_insert_smoke_event", fake_insert_smoke_event)
    monkeypatch.setattr(real_gemini_guarded_smoke.gateway_consumer, "process_inbound_event_id", fake_process_inbound_event_id)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_latest_router_metadata", fake_fetch_latest_router_metadata)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_outbound_messages", fake_fetch_outbound_messages)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_external_commands", fake_fetch_external_commands)
    monkeypatch.setattr(real_gemini_guarded_smoke, "_fetch_graph_run_errors", fake_fetch_graph_run_errors)
    monkeypatch.setattr(real_gemini_guarded_smoke, "OutboundMessageRepository", FakeOutboundRepository)
    monkeypatch.setattr(real_gemini_guarded_smoke, "ExternalCommandRepository", FakeExternalCommandRepository)

    result = asyncio.run(real_gemini_guarded_smoke.run(["--case", "explicit_human_en"]))

    assert calls["outbound_skipped"] == [(55, "manual guarded smoke dry-run; not sent")]
    assert calls["external_skipped"] == [(55, "manual guarded smoke dry-run; external command not executed")]
    assert result["cases"][0]["external_command_count"] == 1
    assert result["cases"][0]["skipped_external_command_count"] == 2
    assert result["cases"][0]["external_commands"] == [
        {"id": 7, "inbound_event_id": 55, "status": "PENDING", "command_type": "human_handoff.requested"}
    ]
    assert result["smoke_success"] is True


def test_real_gemini_guarded_smoke_rejects_unknown_case_without_writes(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "guarded_authoritative"

    async def fail_create_pool(settings):
        raise AssertionError("selection errors must not create a pool")

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fail_create_pool)

    result = asyncio.run(real_gemini_guarded_smoke.run(["--case", "typo"]))

    assert result["error"]["code"] == "unknown_case"
    assert result["total"] == 0
    assert result["smoke_success"] is False


def test_real_gemini_guarded_smoke_rejects_unsupported_case_set_without_pool(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "guarded_authoritative"

    async def fail_create_pool(settings):
        raise AssertionError("selection errors must not create a pool")

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fail_create_pool)

    result = asyncio.run(real_gemini_guarded_smoke.run(["--case-set", "other"]))

    assert result["error"]["code"] == "unsupported_case_set"
    assert result["smoke_success"] is False


def test_real_gemini_guarded_smoke_rejects_empty_limit_without_pool(monkeypatch):
    import asyncio

    from app.workers import real_gemini_guarded_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_router_mode = "guarded_authoritative"

    async def fail_create_pool(settings):
        raise AssertionError("selection errors must not create a pool")

    monkeypatch.setattr(real_gemini_guarded_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(real_gemini_guarded_smoke, "create_pool", fail_create_pool)

    result = asyncio.run(real_gemini_guarded_smoke.run(["--limit", "0"]))

    assert result["error"]["code"] == "empty_case_selection"
    assert result["smoke_success"] is False


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


def test_human_handoff_smoke_default_is_plan_only(monkeypatch):
    import asyncio

    from app.workers import human_handoff_smoke

    calls = {}

    class FakeSettings:
        livechat_handoff_target_group_id = 23
        livechat_handoff_enabled = False

        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs

    class FakePool:
        def close(self) -> None:
            calls["closed"] = True

        async def wait_closed(self) -> None:
            calls["wait_closed"] = True

    async def fake_create_pool(settings):
        calls["pool_settings"] = settings
        return FakePool()

    async def fake_fetch_command(pool, inbound_event_id=None, chat_id=None):
        calls["fetch_scope"] = (inbound_event_id, chat_id)
        return {
            "id": 7,
            "tenant_id": "default",
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "inbound_event_id": 55,
            "command_type": "human_handoff.requested",
            "payload_json": {},
            "status": "PENDING",
        }

    async def fake_fetch_status(pool, conversation_id):
        return "AI_ACTIVE"

    async def fail_dry_run(*args, **kwargs):
        raise AssertionError("plan-only smoke must not consume command")

    async def fail_real(*args, **kwargs):
        raise AssertionError("plan-only smoke must not execute transfer")

    monkeypatch.setattr(human_handoff_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(human_handoff_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_scoped_handoff_command", fake_fetch_command)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_conversation_status", fake_fetch_status)
    monkeypatch.setattr(human_handoff_smoke, "_process_dry_run_command", fail_dry_run)
    monkeypatch.setattr(human_handoff_smoke, "_process_real_command", fail_real)

    result = asyncio.run(human_handoff_smoke.run(["--chat-id", "chat-1"]))

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-human-handoff-smoke",
        "livechat_account_id": "unused-for-human-handoff-smoke",
    }
    assert calls["fetch_scope"] == (None, "chat-1")
    assert result["plan_only"] is True
    assert result["dry_run"] is True
    assert result["command_id"] == 7
    assert result["external_command_status"] == "PENDING"
    assert result["would_send_notice"] is True
    assert result["would_transfer"] is False
    assert result["block_reason"] == "livechat_handoff_enabled is false"
    assert calls["closed"] is True
    assert calls["wait_closed"] is True


def test_human_handoff_smoke_consume_dry_run_is_explicit(monkeypatch):
    import asyncio

    from app.workers import human_handoff_smoke

    calls = {}

    class FakeSettings:
        livechat_handoff_target_group_id = 23
        livechat_handoff_enabled = False

        def __init__(self, **kwargs) -> None:
            pass

    class FakePool:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def fake_create_pool(settings):
        return FakePool()

    command = {
        "id": 8,
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 56,
        "command_type": "human_handoff.requested",
        "payload_json": {},
        "status": "PENDING",
    }

    async def fake_fetch_command(pool, inbound_event_id=None, chat_id=None):
        return command

    async def fake_lease_command(pool, command_id, worker_id, lease_seconds):
        calls["lease"] = (command_id, worker_id, lease_seconds)
        return command

    async def fake_fetch_status(pool, conversation_id):
        return "AI_ACTIVE"

    async def fake_dry_run(command_arg, repository, result_repository, emit_result):
        calls["dry_run"] = (command_arg, emit_result)
        return {"id": command_arg["id"], "command_type": command_arg["command_type"], "status": "DRY_RUN_DONE"}

    async def fail_real(*args, **kwargs):
        raise AssertionError("consume dry-run must not execute transfer")

    monkeypatch.setattr(human_handoff_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(human_handoff_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_scoped_handoff_command", fake_fetch_command)
    monkeypatch.setattr(human_handoff_smoke, "_lease_scoped_handoff_command", fake_lease_command)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_conversation_status", fake_fetch_status)
    monkeypatch.setattr(human_handoff_smoke, "_process_dry_run_command", fake_dry_run)
    monkeypatch.setattr(human_handoff_smoke, "_process_real_command", fail_real)

    result = asyncio.run(human_handoff_smoke.run(["--chat-id", "chat-1", "--consume-dry-run"]))

    assert calls["lease"][0] == 8
    assert calls["dry_run"] == (command, True)
    assert result["plan_only"] is False
    assert result["lease_attempted"] is True
    assert result["lease_acquired"] is True
    assert result["external_command_status"] == "DRY_RUN_DONE"


def test_human_handoff_smoke_execute_calls_real_path(monkeypatch):
    import asyncio

    from app.workers import human_handoff_smoke

    calls = {}

    class FakeSettings:
        livechat_handoff_target_group_id = 23
        livechat_handoff_enabled = True

        def __init__(self, **kwargs) -> None:
            calls["settings_kwargs"] = kwargs

    class FakePool:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def fake_create_pool(settings):
        return FakePool()

    command = {
        "id": 9,
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 57,
        "command_type": "human_handoff.requested",
        "payload_json": {},
        "status": "PENDING",
    }

    async def fake_fetch_command(pool, inbound_event_id=None, chat_id=None):
        return command

    async def fake_lease_command(pool, command_id, worker_id, lease_seconds):
        calls["lease"] = (command_id, worker_id, lease_seconds)
        return {**command, "locked_by": worker_id}

    async def fake_fetch_status(pool, conversation_id):
        return "HUMAN_ACTIVE"

    async def fake_real(command_arg, **kwargs):
        calls["real"] = (command_arg, kwargs["execute_human_handoff"], kwargs["emit_result"])
        return {"id": command_arg["id"], "command_type": command_arg["command_type"], "status": "SENT"}

    async def fail_dry_run(*args, **kwargs):
        raise AssertionError("execute smoke must not call dry-run")

    monkeypatch.setattr(human_handoff_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(human_handoff_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_scoped_handoff_command", fake_fetch_command)
    monkeypatch.setattr(human_handoff_smoke, "_lease_scoped_handoff_command", fake_lease_command)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_conversation_status", fake_fetch_status)
    monkeypatch.setattr(human_handoff_smoke, "_process_real_command", fake_real)
    monkeypatch.setattr(human_handoff_smoke, "_process_dry_run_command", fail_dry_run)

    result = asyncio.run(human_handoff_smoke.run(["--chat-id", "chat-1", "--execute-human-handoff"]))

    assert calls["settings_kwargs"] == {}
    assert calls["lease"][0] == 9
    assert calls["real"][0]["id"] == command["id"]
    assert calls["real"][0]["locked_by"] == result["worker_id"]
    assert calls["real"][1:] == (True, True)
    assert result["lease_attempted"] is True
    assert result["lease_acquired"] is True
    assert result["worker_id"] == result["locked_by"]
    assert result["transfer_attempted"] is True
    assert result["transfer_success"] is True


def test_human_handoff_smoke_execute_blocks_when_scoped_lease_not_acquired(monkeypatch):
    import asyncio

    from app.workers import human_handoff_smoke

    calls = {}

    class FakeSettings:
        livechat_handoff_target_group_id = 23
        livechat_handoff_enabled = True

        def __init__(self, **kwargs) -> None:
            pass

    class FakePool:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def fake_create_pool(settings):
        return FakePool()

    command = {
        "id": 10,
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "inbound_event_id": 58,
        "command_type": "human_handoff.requested",
        "payload_json": {},
        "status": "PENDING",
        "locked_by": "other-worker",
    }

    async def fake_fetch_command(pool, inbound_event_id=None, chat_id=None):
        return command

    async def fake_lease_command(pool, command_id, worker_id, lease_seconds):
        calls["lease"] = (command_id, worker_id, lease_seconds)
        return None

    async def fake_fetch_status(pool, conversation_id):
        return "AI_ACTIVE"

    async def fail_real(*args, **kwargs):
        raise AssertionError("must not execute without scoped lease")

    monkeypatch.setattr(human_handoff_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(human_handoff_smoke, "create_pool", fake_create_pool)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_scoped_handoff_command", fake_fetch_command)
    monkeypatch.setattr(human_handoff_smoke, "_lease_scoped_handoff_command", fake_lease_command)
    monkeypatch.setattr(human_handoff_smoke, "_fetch_conversation_status", fake_fetch_status)
    monkeypatch.setattr(human_handoff_smoke, "_process_real_command", fail_real)

    result = asyncio.run(human_handoff_smoke.run(["--chat-id", "chat-1", "--execute-human-handoff"]))

    assert calls["lease"][0] == 10
    assert result["lease_attempted"] is True
    assert result["lease_acquired"] is False
    assert result["lease_blocked_reason"] == "command is locked or no longer pending"
    assert result["locked_by"] == "other-worker"
    assert result["transfer_attempted"] is False


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
        "intent_mode": "guarded_authoritative",
        "intent_min_confidence": 0.75,
        "intent_fallback_to_deterministic": True,
        "sop_slot_enabled": False,
        "sop_slot_min_confidence": 0.7,
        "fallback_enabled": False,
        "shadow_active": True,
    }


def test_gateway_llm_summary_reports_final_reply_when_enabled():
    from app.workers.gateway_consumer import _build_llm_summary

    class FakeSettings:
        llm_provider = "gemini"
        llm_final_reply_enabled = True
        llm_final_reply_min_confidence = 0.81
        llm_final_reply_fallback_enabled = True

    summary = _build_llm_summary(FakeSettings())

    assert summary["final_reply_enabled"] is True
    assert summary["final_reply_min_confidence"] == 0.81
    assert summary["final_reply_fallback_enabled"] is True


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
