from app.core.settings import Settings


def test_settings_defaults():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
    )

    assert settings.livechat_api_base == "https://api.livechatinc.com/v3.6"
    assert settings.poll_seconds == 5
    assert settings.mysql_port == 3306


def test_build_app_has_health_route():
    from app.api.main import build_app

    app = build_app()

    paths = {route.path for route in app.routes}
    assert "/healthz" in paths


def test_load_sql_files_in_order():
    from pathlib import Path

    from app.db.bootstrap import load_sql_files

    files = load_sql_files(Path("sql"))

    assert [item.name for item in files] == [
        "001_inbound_events.sql",
        "002_conversation_states.sql",
        "003_outbound_messages.sql",
    ]


def test_outbound_messages_schema_has_inbound_action_idempotency_key():
    from pathlib import Path

    sql = Path("sql/003_outbound_messages.sql").read_text()

    assert "UNIQUE KEY uk_inbound_action (inbound_event_id, action_type)" in sql


def test_bootstrap_worker_does_not_require_livechat_credentials(monkeypatch):
    import asyncio

    from app.workers import bootstrap_db

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

    async def fake_bootstrap_database(pool, sql_path):
        calls["bootstrapped"] = str(sql_path)

    monkeypatch.setattr(bootstrap_db, "Settings", FakeSettings)
    monkeypatch.setattr(bootstrap_db, "create_pool", fake_create_pool)
    monkeypatch.setattr(bootstrap_db, "bootstrap_database", fake_bootstrap_database)

    asyncio.run(bootstrap_db.run())

    assert calls["settings_kwargs"] == {
        "livechat_agent_access_token": "unused-for-bootstrap",
        "livechat_account_id": "unused-for-bootstrap",
    }
    assert calls["bootstrapped"] == "sql"
    assert calls["closed"] is True
    assert calls["wait_closed"] is True
