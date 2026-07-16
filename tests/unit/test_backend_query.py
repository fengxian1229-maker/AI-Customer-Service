import asyncio

import pytest


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
        "totp_secret": None,
        "login_merchant": "COP",
        "request_timeout_seconds": 10.0,
        "default_lookback_days": 30,
        "fallback_lookback_days": 90,
        "source": "env_default",
    }
    values.update(overrides)
    return BackendConfig(**values)


def test_backend_config_repr_redacts_secrets():
    config = make_config(
        authorization="Bearer secret-token",
        login_password="secret-password",
        livechat_group_id=13,
        platform="PAG99",
    )

    rendered = repr(config)
    safe = config.sanitized()

    assert "secret-token" not in rendered
    assert "secret-password" not in rendered
    assert safe["authorization"] == "<redacted>"
    assert safe["login_password"] == "<redacted>"
    assert safe["livechat_group_id"] == 13
    assert safe["platform"] == "PAG99"


def test_backend_config_repr_redacts_totp_secret():
    config = make_config(totp_secret="JBSWY3DPEHPK3PXP")

    rendered = repr(config)
    safe = config.sanitized()

    assert "JBSWY3DPEHPK3PXP" not in rendered
    assert safe["totp_secret"] == "<redacted>"


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


def test_tenant_backend_config_resolver_env_default_source_and_missing_config(monkeypatch):
    from app.backends.resolver import BackendConfigError, TenantBackendConfigResolver
    from app.core.settings import Settings

    for key in (
        "BACKEND_BASE_URL",
        "BACKEND_AUTHORIZATION",
        "BACKEND_MERCHANT_CODE",
        "BACKEND_LOGIN_OPERATOR",
        "BACKEND_LOGIN_PASSWORD",
        "BACKEND_TOTP_SECRET",
        "BACKEND_LOGIN_MERCHANT",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(
        livechat_agent_access_token="unused",
        livechat_account_id="unused",
        backend_query_enabled=True,
        backend_provider_type="tac",
        backend_base_url=None,
        backend_authorization=None,
        backend_merchant_code=None,
        backend_login_operator=None,
        backend_login_password=None,
        backend_totp_secret=None,
        backend_login_merchant=None,
    )

    try:
        TenantBackendConfigResolver(settings).resolve(tenant_id="tenant-a")
    except BackendConfigError as exc:
        assert exc.code == "FAILED_CONFIG"
        assert "backend_base_url" in str(exc)
    else:
        raise AssertionError("expected missing config")


def test_tenant_backend_config_resolver_accepts_operator_with_totp_without_password():
    from app.backends.resolver import TenantBackendConfigResolver
    from app.core.settings import Settings

    config = TenantBackendConfigResolver(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=True,
            backend_provider_type="tac",
            backend_base_url="https://tac.example",
            backend_authorization=None,
            backend_merchant_code="COP",
            backend_login_operator="operator-a",
            backend_login_password=None,
            backend_totp_secret="JBSWY3DPEHPK3PXP",
            backend_login_merchant="COP",
        )
    ).resolve(tenant_id="tenant-a")

    assert config.login_operator == "operator-a"
    assert config.login_password is None
    assert config.totp_secret == "JBSWY3DPEHPK3PXP"


def make_enabled_backend_settings(**overrides):
    from app.core.settings import Settings

    values = {
        "livechat_agent_access_token": "unused",
        "livechat_account_id": "unused",
        "backend_query_enabled": True,
        "backend_provider_type": "tac",
        "backend_base_url": "https://tac.example",
        "backend_authorization": "token",
        "backend_merchant_code": "wrong-global-default",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("group_id", "platform", "expected_platform", "expected_merchant"),
    [
        (2, "JUE999", "JUE999", "juecopf1"),
        (11, "JG7", "JG7", "jgcops1"),
        (12, "GNA777", "GNA777", "gnacops1"),
        (13, " pag99 ", "PAG99", "pagcops1"),
        (23, "TEST", "TEST", "zapcops1"),
        (24, "CUM777", "CUM777", "cumcops1"),
        (25, "CON777", "CON777", "concops1"),
        (28, "ZAP69", "ZAP69", "zapcops1"),
    ],
)
def test_resolver_records_authoritative_route(group_id, platform, expected_platform, expected_merchant):
    from app.backends.resolver import TenantBackendConfigResolver

    resolver = TenantBackendConfigResolver(make_enabled_backend_settings())
    config = resolver.resolve(
        tenant_id="default",
        channel_type="livechat",
        livechat_group_id=group_id,
        platform=platform,
    )

    assert config.livechat_group_id == group_id
    assert config.platform == expected_platform
    assert config.merchant_code == expected_merchant
    assert config.source == f"livechat_group:{group_id}"


def test_resolver_derives_platform_when_payload_omits_it():
    from app.backends.resolver import TenantBackendConfigResolver

    resolver = TenantBackendConfigResolver(make_enabled_backend_settings())
    config = resolver.resolve(
        tenant_id="default",
        channel_type="livechat",
        livechat_group_id="13",
        platform=None,
    )

    assert config.livechat_group_id == 13
    assert config.platform == "PAG99"


@pytest.mark.parametrize(
    ("group_id", "platform", "message"),
    [
        (None, "PAG99", "livechat_group_id is required"),
        (0, "PAG99", "must be a positive integer"),
        ("bad", "PAG99", "must be a positive integer"),
        (13.0, "PAG99", "must be a positive integer"),
        (13.5, "PAG99", "must be a positive integer"),
        (True, "PAG99", "must be a positive integer"),
        (999, "PAG99", "has no backend merchant mapping"),
        (13, "CUM777", "does not match"),
    ],
)
def test_resolver_rejects_invalid_livechat_routing(group_id, platform, message):
    from app.backends.resolver import BackendConfigError, TenantBackendConfigResolver

    resolver = TenantBackendConfigResolver(make_enabled_backend_settings())
    with pytest.raises(BackendConfigError, match=message) as exc_info:
        resolver.resolve(
            tenant_id="default",
            channel_type="livechat",
            livechat_group_id=group_id,
            platform=platform,
        )
    assert exc_info.value.code == "FAILED_CONFIG"
    message_text = str(exc_info.value)
    assert "livechat_group_id=" in message_text
    assert "platform=" in message_text


def test_resolver_keeps_env_default_for_non_livechat_probe():
    from app.backends.resolver import TenantBackendConfigResolver

    config = TenantBackendConfigResolver(make_enabled_backend_settings()).resolve(
        tenant_id="default",
        channel_type="probe",
    )

    assert config.merchant_code == "wrong-global-default"
    assert config.source == "env_default"
    assert config.livechat_group_id is None
    assert config.platform is None


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


def test_generate_totp_matches_rfc_6238_sha1_vector():
    from app.backends.tac_client import generate_totp

    # RFC 6238 test secret: b"12345678901234567890".
    assert generate_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", for_time=59, digits=8) == "94287082"


def test_tac_login_otp_posts_otp_endpoint_and_redacts_secret():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport([{"token": "otp-secret-token"}])
    client = TacBackendClient(
        make_config(authorization=None, login_password=None, totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        transport=transport,
    )

    token = client.login_otp()

    assert token == "otp-secret-token"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://tac.example/tac/api/login/otp"
    assert call["headers"]["Merchant"] == "COP"
    assert call["headers"]["MerchantCode"] == "COP"
    assert call["headers"]["Accept"] == "application/json, text/plain, */*"
    assert call["headers"]["language"] == "zh_CN"
    assert call["headers"]["User-Agent"] == "Mozilla/5.0"
    assert b"operator-a" in call["body"]
    assert b'"code":' in call["body"]
    assert b"GEZDGNBVGY3TQOJQ" not in call["body"]
    assert "GEZDGNBVGY3TQOJQ" not in repr(client)


def test_tac_backend_preflight_disabled_does_not_login(monkeypatch):
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    class FailingFactory:
        def create(self, config):
            raise AssertionError("preflight must not create TAC client when disabled")

    monkeypatch.setenv("ENABLE_BACKEND_LOOKUP", "true")
    for key in (
        "BACKEND_BASE_URL",
        "BACKEND_AUTHORIZATION",
        "BACKEND_MERCHANT_CODE",
        "BACKEND_LOGIN_OPERATOR",
        "BACKEND_LOGIN_PASSWORD",
        "BACKEND_TOTP_SECRET",
        "BACKEND_LOGIN_MERCHANT",
    ):
        monkeypatch.delenv(key, raising=False)
    result = tac_backend_probe.run_preflight(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=False,
            backend_base_url=None,
            backend_authorization=None,
            backend_merchant_code=None,
            backend_login_operator=None,
            backend_login_password=None,
            backend_totp_secret=None,
            backend_login_merchant=None,
        ),
        factory=FailingFactory(),
    )

    assert result["backend_query_enabled"] is False
    assert result["has_authorization"] is False
    assert result["has_login_password"] is False
    assert result["has_totp_secret"] is False
    assert result["login_attempted"] is False
    assert result["safe_to_probe"] is False
    assert result["settings_warning"] == [
        "ENABLE_BACKEND_LOOKUP is not used by this app; set BACKEND_QUERY_ENABLED=true"
    ]
    assert result["exit_code"] == 2
    assert result["terminal_status"] == "DISABLED"


def test_tac_backend_preflight_login_success_is_sanitized():
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    class FakeClient:
        def login_password(self):
            return "secret-login-token"

    class FakeFactory:
        def create(self, config):
            return FakeClient()

    result = tac_backend_probe.run_preflight(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=True,
            backend_provider_type="tac",
            backend_base_url="https://secret.example",
            backend_merchant_code="MERCHANT",
            backend_login_operator="operator-secret",
            backend_login_password="password-secret",
            backend_totp_secret=None,
            backend_login_merchant="MERCHANT",
            backend_authorization=None,
        ),
        factory=FakeFactory(),
    )

    assert result["login_attempted"] is True
    assert result["login_success"] is True
    assert result["safe_to_probe"] is True
    assert result["exit_code"] == 0
    assert result["terminal_status"] == "OK"
    rendered = str(result)
    assert "secret-login-token" not in rendered
    assert "password-secret" not in rendered
    assert "operator-secret" not in rendered
    assert "https://secret.example" not in rendered


def test_tac_backend_preflight_otp_login_success_is_sanitized():
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    class FakeClient:
        def login_otp(self):
            return "secret-otp-token"

    class FakeFactory:
        def create(self, config):
            assert config.totp_secret == "secret-base32"
            return FakeClient()

    result = tac_backend_probe.run_preflight(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=True,
            backend_provider_type="tac",
            backend_base_url="https://secret.example",
            backend_merchant_code="MERCHANT",
            backend_login_operator="operator-secret",
            backend_login_password=None,
            backend_totp_secret="secret-base32",
            backend_login_merchant="MERCHANT",
            backend_authorization=None,
        ),
        factory=FakeFactory(),
    )

    assert result["login_attempted"] is True
    assert result["login_method"] == "otp"
    assert result["login_success"] is True
    assert result["safe_to_probe"] is True
    rendered = str(result)
    assert "secret-otp-token" not in rendered
    assert "secret-base32" not in rendered
    assert "operator-secret" not in rendered


def test_tac_backend_preflight_main_returns_zero_when_preflight_ok(monkeypatch, capsys):
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    monkeypatch.setattr(tac_backend_probe, "Settings", lambda: Settings(livechat_agent_access_token="unused", livechat_account_id="unused"))
    monkeypatch.setattr(
        tac_backend_probe,
        "run_preflight",
        lambda settings: {"preflight_status": "OK", "exit_code": 0, "terminal_status": "OK"},
    )

    assert tac_backend_probe.main(["preflight"]) == 0
    assert '"exit_code": 0' in capsys.readouterr().out


def test_tac_backend_preflight_missing_config_fails_without_login():
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    class FailingFactory:
        def create(self, config):
            raise AssertionError("preflight must not login when required config is missing")

    result = tac_backend_probe.run_preflight(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=True,
            backend_provider_type="tac",
            backend_base_url="",
            backend_merchant_code="MERCHANT",
            backend_authorization=None,
            backend_login_operator="operator",
            backend_login_password="password",
        ),
        factory=FailingFactory(),
    )

    assert result["preflight_status"] == "FAILED_CONFIG"
    assert result["login_attempted"] is False
    assert result["safe_to_probe"] is False
    assert result["exit_code"] == 2
    assert result["terminal_status"] == "FAILED_CONFIG"
    assert "backend_base_url" in result["missing_config"]


def test_tac_backend_preflight_login_failed_exit_code_and_redacts_url():
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    class FailingClient:
        def login_password(self):
            raise RuntimeError("failed at https://backend.secret.example/login Authorization: Bearer token password=abc")

    class FakeFactory:
        def create(self, config):
            return FailingClient()

    result = tac_backend_probe.run_preflight(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=True,
            backend_provider_type="tac",
            backend_base_url="https://backend.secret.example",
            backend_merchant_code="MERCHANT",
            backend_login_operator="operator-secret",
            backend_login_password="password-secret",
            backend_login_merchant="MERCHANT",
        ),
        factory=FakeFactory(),
    )

    rendered = str(result)
    assert result["preflight_status"] == "LOGIN_FAILED"
    assert result["exit_code"] == 3
    assert result["terminal_status"] == "LOGIN_FAILED"
    assert "backend.secret.example" not in rendered
    assert "Bearer token" not in rendered
    assert "password=abc" not in rendered


def test_tac_backend_preflight_unsupported_provider_exit_code():
    from app.core.settings import Settings
    from app.workers import tac_backend_probe

    result = tac_backend_probe.run_preflight(
        Settings(
            livechat_agent_access_token="unused",
            livechat_account_id="unused",
            backend_query_enabled=True,
            backend_provider_type="crawler",
        )
    )

    assert result["preflight_status"] == "UNSUPPORTED_PROVIDER"
    assert result["exit_code"] == 2
    assert result["terminal_status"] == "UNSUPPORTED_PROVIDER"


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


def test_tac_api_get_refreshes_invalid_token_with_otp_when_secret_configured():
    from app.backends.tac_client import TacBackendClient

    transport = FakeTransport(
        [
            {"errorCode": "INVALID_TOKEN"},
            {"token": "Bearer otp-token"},
            {"success": True, "value": []},
        ]
    )
    client = TacBackendClient(
        make_config(login_password=None, totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        transport=transport,
    )

    result = client.api_get("/tac/api/relay/get/player-search-non-bankcard", {"data": "andy", "pageNo": 1})

    assert result == {"success": True, "value": []}
    assert transport.calls[1]["method"] == "POST"
    assert transport.calls[1]["url"] == "https://tac.example/tac/api/login/otp"
    assert transport.calls[2]["headers"]["Authorization"] == "Bearer otp-token"


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


def test_backend_query_service_generates_structured_reply_intent():
    from app.backends.config import BackendConfig
    from app.services.backend_query_service import BackendQueryService

    class FakeResolver:
        def __init__(self):
            self.calls = []

        def resolve(
            self,
            tenant_id,
            channel_type=None,
            channel_instance_id=None,
            livechat_group_id=None,
            platform=None,
        ):
            self.calls.append(
                (tenant_id, channel_type, channel_instance_id, livechat_group_id, platform)
            )
            return make_config(
                merchant_code="pagcops1",
                source="livechat_group:13",
                livechat_group_id=13,
                platform="PAG99",
            )

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
    resolver = FakeResolver()
    result = BackendQueryService(resolver, FakeFactory(active_response)).execute(
        {
            "intent": "withdrawal_blocked_or_rollover",
            "account_or_phone": "3107939521",
            "livechat_group_id": "13",
            "platform": " pag99 ",
        },
        tenant_id="tenant-a",
        channel_type="livechat",
        channel_instance_id="chat-1",
    )

    assert resolver.calls == [("tenant-a", "livechat", "chat-1", "13", " pag99 ")]
    assert result["status"] == "success"
    assert "answer" not in result
    assert result["reply_intent"] == "backend_turnover_remaining"
    assert result["reply_facts"] == {"remaining_turnover": "88.5"}
    assert result["livechat_group_id"] == 13
    assert result["platform"] == "PAG99"
    assert result["merchant_code"] == "pagcops1"
    assert result["config_source"] == "livechat_group:13"
    assert "authorization" not in str(result).lower()

    not_found = BackendQueryService(FakeResolver(), FakeFactory({"player_found": False})).execute(
        {"intent": "withdrawal_blocked_or_rollover", "account_or_phone": "andy"},
        tenant_id=None,
    )
    assert "answer" not in not_found
    assert not_found["reply_intent"] == "backend_player_not_found"


def test_backend_query_service_config_failure_does_not_create_provider():
    from app.backends.resolver import TenantBackendConfigResolver
    from app.services.backend_query_service import BackendQueryService

    class ForbiddenFactory:
        def create(self, config):
            raise AssertionError("provider must not be created")

    result = BackendQueryService(
        TenantBackendConfigResolver(make_enabled_backend_settings()),
        ForbiddenFactory(),
    ).execute(
        {
            "intent": "withdrawal_blocked_or_rollover",
            "account_or_phone": "andy",
            "livechat_group_id": 13.5,
            "platform": " pag99 ",
        },
        tenant_id="default",
        channel_type="livechat",
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "FAILED_CONFIG"
    assert "livechat_group_id=13.5" in result["error_message"]
    assert "platform='PAG99'" in result["error_message"]


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
                    "payload_json": {
                        "intent": "withdrawal_blocked_or_rollover",
                        "account_or_phone": "andy",
                        "livechat_group_id": "13",
                        "platform": " pag99 ",
                        "identity_source": "user_text",
                        "reply_language": "zh-Hans",
                        "conversation_language": "zh-Hans",
                        "detected_language": "zh-Hans",
                        "raw_user_input": "提款不了，用户名是 andy",
                        "rewritten_question": "提款不了，用户名是 andy",
                    },
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
            assert channel_type == "livechat"
            assert payload["livechat_group_id"] == "13"
            assert payload["platform"] == " pag99 "
            return {
                "status": "success",
                "answer": "后台查询完成",
                "query": {"intent": payload["intent"]},
                "livechat_group_id": 13,
                "platform": "PAG99",
                "merchant_code": "pagcops1",
                "config_source": "livechat_group:13",
            }

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
    assert result_repository.inserted[0]["result_json"]["livechat_group_id"] == 13
    assert result_repository.inserted[0]["result_json"]["platform"] == "PAG99"
    assert result_repository.inserted[0]["result_json"]["merchant_code"] == "pagcops1"
    assert result_repository.inserted[0]["result_json"]["config_source"] == "livechat_group:13"
    assert result_repository.inserted[0]["result_json"]["identity_source"] == "user_text"
    assert result_repository.inserted[0]["result_json"]["reply_language"] == "zh-Hans"
    assert result_repository.inserted[0]["result_json"]["conversation_language"] == "zh-Hans"
    assert result_repository.inserted[0]["result_json"]["detected_language"] == "zh-Hans"
    assert result_repository.inserted[0]["result_json"]["raw_user_input"] == "提款不了，用户名是 andy"
    assert result_repository.inserted[0]["result_json"]["rewritten_question"] == "提款不了，用户名是 andy"


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


def test_external_command_worker_execute_backend_config_failure_emits_failed_result():
    from app.core.settings import Settings
    from app.workers.external_command_worker import process_pending_commands

    class FakeCommandRepository:
        def __init__(self):
            self.statuses = []

        async def lease_pending(self, limit, worker_id, lease_seconds):
            return [
                {
                    "id": 79,
                    "tenant_id": "tenant-a",
                    "conversation_id": "livechat:chat-1",
                    "chat_id": "chat-1",
                    "thread_id": "thread-1",
                    "inbound_event_id": 179,
                    "command_type": "backend.query",
                    "payload_json": {
                        "intent": "withdrawal_blocked_or_rollover",
                        "account_or_phone": "andy",
                        "livechat_group_id": 13,
                        "platform": "PAG99",
                        "reply_language": "zh-Hans",
                    },
                }
            ]

        async def mark_status(self, command_id, status, error=None):
            self.statuses.append((command_id, status, error))

    class FakeResultRepository:
        def __init__(self):
            self.inserted = []

        async def insert_idempotent(self, result):
            self.inserted.append(result)
            return {"inserted": True, "duplicate": False, "id": 90}

    result_repository = FakeResultRepository()
    result = asyncio.run(
        process_pending_commands(
            FakeCommandRepository(),
            result_repository=result_repository,
            dry_run=False,
            execute_backend=True,
            emit_result=True,
            settings=Settings(livechat_agent_access_token="unused", livechat_account_id="unused", backend_query_enabled=False),
            worker_id="worker-a",
        )
    )

    assert result[0]["status"] == "FAILED_CONFIG"
    assert result_repository.inserted[0]["result_type"] == "backend.query.result"
    assert result_repository.inserted[0]["result_json"] == {
        "status": "failed",
        "error_code": "FAILED_CONFIG",
        "error_message": "backend_query_enabled is false",
        "livechat_group_id": 13,
        "platform": "PAG99",
        "reply_language": "zh-Hans",
    }
