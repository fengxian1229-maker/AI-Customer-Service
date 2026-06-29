import asyncio


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, body=None, timeout_seconds=20.0):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected backend request")
        return self.responses.pop(0)


def make_config(**overrides):
    from app.backends.config import BackendConfig

    values = {
        "provider_type": "tac",
        "base_url": "https://tac.example",
        "authorization": "Bearer old-token",
        "merchant_code": "COP",
        "login_operator": "operator-a",
        "login_password": "password-a",
        "login_merchant": "COP",
        "request_timeout_seconds": 10.0,
        "default_lookback_days": 30,
        "fallback_lookback_days": 90,
        "source": "env_default",
    }
    values.update(overrides)
    return BackendConfig(**values)


def test_backend_config_repr_redacts_secrets():
    config = make_config(authorization="Bearer secret-token", login_password="secret-password")

    rendered = repr(config)
    safe = config.sanitized()

    assert "secret-token" not in rendered
    assert "secret-password" not in rendered
    assert safe["authorization"] == "<redacted>"
    assert safe["login_password"] == "<redacted>"


def test_tenant_backend_config_resolver_default_disabled_fails_config():
    from app.backends.resolver import BackendConfigError, TenantBackendConfigResolver
    from app.core.settings import Settings

    settings = Settings(
        livechat_agent_access_token="unused",
        livechat_account_id="unused",
        backend_query_enabled=False,
    )

    try:
        TenantBackendConfigResolver(settings).resolve(tenant_id="tenant-a")
    except BackendConfigError as exc:
        assert exc.code == "FAILED_CONFIG"
        assert "backend_query_enabled is false" in str(exc)
    else:
        raise AssertionError("expected BackendConfigError")


def test_tenant_backend_config_resolver_env_default_source_and_missing_config():
    from app.backends.resolver import BackendConfigError, TenantBackendConfigResolver
    from app.core.settings import Settings

    settings = Settings(
        livechat_agent_access_token="unused",
        livechat_account_id="unused",
        backend_query_enabled=True,
        backend_provider_type="tac",
    )

    try:
        TenantBackendConfigResolver(settings).resolve(tenant_id="tenant-a")
    except BackendConfigError as exc:
        assert exc.code == "FAILED_CONFIG"
        assert "backend_base_url" in str(exc)
    else:
        raise AssertionError("expected missing config")


def test_tac_login_password_posts_login_endpoint_and_redacts_token():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport([{"token": "new-secret-token"}])
    client = TacBackendClient(make_config(authorization=None), transport=transport)

    token = client.login_password()

    assert token == "new-secret-token"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://tac.example/tac/api/login/password"
    assert call["headers"]["Merchant"] == "COP"
    assert call["headers"]["Referer"] == "https://tac.example/COP"
    assert b"operator-a" in call["body"]
    assert b"password-a" in call["body"]
    assert "new-secret-token" not in repr(client)


def test_tac_api_get_builds_headers_refreshes_invalid_token_once():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport(
        [
            {"errorCode": "INVALID_TOKEN"},
            {"token": "Bearer fresh-token"},
            {"success": True, "value": []},
        ]
    )
    client = TacBackendClient(make_config(), transport=transport)

    result = client.api_get("/tac/api/relay/get/player-search-non-bankcard", {"data": "andy", "pageNo": 1})

    assert result == {"success": True, "value": []}
    first_get = transport.calls[0]
    assert first_get["method"] == "GET"
    assert "data=andy" in first_get["url"]
    assert "pageNo=1" in first_get["url"]
    assert first_get["headers"]["Authorization"] == "Bearer old-token"
    assert first_get["headers"]["Merchant"] == "COP"
    assert first_get["headers"]["merchantCode"] == "COP"
    assert first_get["headers"]["Language"] == "zh_CN"
    assert first_get["headers"]["environment"] == "TCG1"
    assert first_get["headers"]["platform"] == "TCG"
    assert transport.calls[1]["method"] == "POST"
    assert transport.calls[2]["headers"]["Authorization"] == "Bearer fresh-token"


def test_tac_query_player_user_tries_username_then_mobile_and_prefers_exact_match():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport(
        [
            {"success": True, "value": []},
            {
                "success": True,
                "data": {"records": [{"customerId": "1", "customerName": "other"}, {"id": "2", "mobile": "13800138000"}]},
            },
        ]
    )
    client = TacBackendClient(make_config(), transport=transport)

    result = client.query_player_user("13800138000")

    assert result["customer_id"] == "2"
    assert result["search_code"] == "MOBILE"
    assert "searchCode=USERNAME" in transport.calls[0]["url"]
    assert "searchCode=MOBILE" in transport.calls[1]["url"]


def test_tac_query_deposit_and_match_deposit_parity():
    from app.backends.tac_client import TacBackendClient, match_deposit

    transport = FakeTransport([{"success": True, "value": [{"requestAmount": "100.00", "bankRef": "ABC-123"}]}])
    client = TacBackendClient(make_config(), transport=transport)

    records = client.query_deposit("andy", "2026-06-01", "2026-06-02")

    url = transport.calls[0]["url"]
    assert "/tac/api/relay/get/pv2-mcs-internal-v3-player-deposit-search?" in url
    assert "dateFrom=2026-06-01+00%3A00%3A00" in url
    assert "dateTo=2026-06-02+23%3A59%3A59" in url
    assert "pid=610151" in url
    assert records[0]["bankRef"] == "ABC-123"
    assert match_deposit(records, 100.004, target_ref="abc") == records[0]
    assert match_deposit(records, 100.02, target_ref="abc") is None


def test_tac_turnover_requirement_queries_windows_and_incomplete_statuses():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport(
        [
            {"success": True, "value": [{"customerId": "cust-1", "customerName": "andy"}]},
            {
                "success": True,
                "value": [
                    {
                        "statusName": "未完成",
                        "remainingTurnover": "88.50",
                        "requiredTurnover": "100",
                        "validTurnover": "11.50",
                    }
                ],
            },
        ]
    )
    client = TacBackendClient(make_config(), transport=transport)

    result = client.query_turnover_requirement("andy")

    assert result["player_found"] is True
    assert result["customer_id"] == "cust-1"
    assert result["active_requirements_count"] == 1
    assert result["remaining_turnover"] == 88.5
    assert result["is_met"] is False
    assert result["query_windows"][0]["query_mode"] == "custom_recent"


def test_tac_contribution_uses_relay_post_path_with_get():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport([{"success": True, "value": [{"customerName": "andy"}]}])
    client = TacBackendClient(make_config(), transport=transport)

    records = client.query_player_contribution("andy", "2026-06-01", "2026-06-02")

    assert records == [{"customerName": "andy"}]
    assert transport.calls[0]["method"] == "GET"
    assert "/tac/api/relay/post/ods-v2-report-player-contributionv2-search?" in transport.calls[0]["url"]
    assert "subordinateNames=andy" in transport.calls[0]["url"]


def test_backend_provider_factory_only_supports_explicit_tac():
    from app.backends.factory import BackendProviderFactory, UnsupportedBackendProviderError
    from app.backends.tac_client import TacBackendClient

    assert isinstance(BackendProviderFactory().create(make_config(provider_type="tac")), TacBackendClient)
    try:
        BackendProviderFactory().create(make_config(provider_type="crawler"))
    except UnsupportedBackendProviderError as exc:
        assert "crawler" in str(exc)
    else:
        raise AssertionError("expected unsupported provider")


def test_backend_query_service_generates_deterministic_answers():
    from app.backends.config import BackendConfig
    from app.services.backend_query_service import BackendQueryService

    class FakeResolver:
        def resolve(self, tenant_id, channel_type=None, channel_instance_id=None):
            return make_config()

    class FakeProvider:
        def __init__(self, response):
            self.response = response

        def query_turnover_requirement(self, account_or_phone):
            return self.response

    class FakeFactory:
        def __init__(self, response):
            self.response = response

        def create(self, config: BackendConfig):
            return FakeProvider(self.response)

    active_response = {
        "player_found": True,
        "active_requirements_count": 1,
        "remaining_turnover": 88.5,
        "is_met": False,
        "records": [],
        "query_windows": [],
    }
    result = BackendQueryService(FakeResolver(), FakeFactory(active_response)).execute(
        {"intent": "withdrawal_blocked_or_rollover", "account_or_phone": "andy"},
        tenant_id="tenant-a",
    )

    assert result["status"] == "success"
    assert "剩余流水约为 88.5" in result["answer"]
    assert result["config_source"] == "env_default"
    assert "authorization" not in str(result).lower()

    not_found = BackendQueryService(FakeResolver(), FakeFactory({"player_found": False})).execute(
        {"intent": "withdrawal_blocked_or_rollover", "account_or_phone": "andy"},
        tenant_id=None,
    )
    assert "未查询到" in not_found["answer"]


def test_external_command_worker_execute_backend_success_emits_result(monkeypatch):
    from app.core.settings import Settings
    from app.workers import external_command_worker

    class FakeCommandRepository:
        def __init__(self):
            self.sent = []

        async def lease_pending(self, limit, worker_id, lease_seconds):
            return [
                {
                    "id": 77,
                    "tenant_id": "tenant-a",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 177,
                    "command_type": "backend.query",
                    "payload_json": {"intent": "withdrawal_blocked_or_rollover", "account_or_phone": "andy"},
                }
            ]

        async def mark_sent(self, command_id):
            self.sent.append(command_id)

    class FakeResultRepository:
        def __init__(self):
            self.inserted = []

        async def insert_idempotent(self, result):
            self.inserted.append(result)
            return {"inserted": True, "duplicate": False, "id": 88}

    class FakeService:
        def execute(self, payload, tenant_id=None, channel_type=None, channel_instance_id=None):
            assert tenant_id == "tenant-a"
            return {"status": "success", "answer": "后台查询完成", "query": {"intent": payload["intent"]}}

    monkeypatch.setattr(external_command_worker, "_build_backend_query_service", lambda settings: FakeService())
    repository = FakeCommandRepository()
    result_repository = FakeResultRepository()

    result = asyncio.run(
        external_command_worker.process_pending_commands(
            repository,
            result_repository=result_repository,
            dry_run=False,
            execute_backend=True,
            emit_result=True,
            settings=Settings(livechat_agent_access_token="unused", livechat_account_id="unused", backend_query_enabled=True),
            worker_id="worker-a",
        )
    )

    assert repository.sent == [77]
    assert result[0]["status"] == "SENT"
    assert result_repository.inserted[0]["result_type"] == "backend.query.result"
    assert result_repository.inserted[0]["result_json"]["answer"] == "后台查询完成"


def test_external_command_worker_execute_backend_disabled_returns_failed_config():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self):
            self.statuses = []

        async def lease_pending(self, limit, worker_id, lease_seconds):
            return [
                {
                    "id": 78,
                    "tenant_id": "tenant-a",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 178,
                    "command_type": "backend.query",
                    "payload_json": {"intent": "withdrawal_blocked_or_rollover", "account_or_phone": "andy"},
                }
            ]

        async def mark_status(self, command_id, status, error=None):
            self.statuses.append((command_id, status, error))

    result = asyncio.run(
        process_pending_commands(
            FakeCommandRepository(),
            dry_run=False,
            execute_backend=True,
            settings=Settings(livechat_agent_access_token="unused", livechat_account_id="unused", backend_query_enabled=False),
            worker_id="worker-a",
        )
    )

    assert result[0]["status"] == "FAILED_CONFIG"
    assert "backend_query_enabled is false" in result[0]["error"]
