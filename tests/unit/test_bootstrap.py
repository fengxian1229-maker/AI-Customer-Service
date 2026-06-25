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
        "004_add_workflow_stage.sql",
        "005_external_commands.sql",
        "006_external_command_results.sql",
    ]


def test_outbound_messages_schema_has_inbound_action_idempotency_key():
    from pathlib import Path

    sql = Path("sql/003_outbound_messages.sql").read_text()

    assert "UNIQUE KEY uk_inbound_action (inbound_event_id, action_type)" in sql


def test_conversation_states_schema_has_workflow_stage():
    from pathlib import Path

    sql = Path("sql/002_conversation_states.sql").read_text()

    assert "workflow_stage VARCHAR(128) NULL" in sql


def test_external_commands_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/005_external_commands.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS external_commands" in sql
    assert "UNIQUE KEY uk_external_commands_dedup (dedup_key)" in sql
    assert "KEY idx_external_commands_status_created (status, created_at)" in sql
    assert "KEY idx_external_commands_conversation (conversation_id)" in sql
    assert "KEY idx_external_commands_inbound_event (inbound_event_id)" in sql


def test_external_command_results_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/006_external_command_results.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS external_command_results" in sql
    assert "UNIQUE KEY uk_external_command_results_dedup (dedup_key)" in sql
    assert "KEY idx_external_command_results_status_created (status, created_at)" in sql
    assert "KEY idx_external_command_results_external_command (external_command_id)" in sql
    assert "KEY idx_external_command_results_conversation (conversation_id)" in sql
    assert "KEY idx_external_command_results_inbound_event (inbound_event_id)" in sql


def test_bootstrap_adds_missing_workflow_stage_for_mysql():
    import asyncio

    from app.db.bootstrap import ensure_conversation_states_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []

        async def execute(self, sql):
            self.executed.append(sql)

        async def fetchall(self):
            return [("conversation_id",), ("slot_memory",)]

    cursor = FakeCursor()

    asyncio.run(ensure_conversation_states_compat(cursor))

    assert "ALTER TABLE conversation_states ADD COLUMN workflow_stage VARCHAR(128) NULL" in cursor.executed


def test_bootstrap_does_not_add_existing_workflow_stage_for_mysql():
    import asyncio

    from app.db.bootstrap import ensure_conversation_states_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []

        async def execute(self, sql):
            self.executed.append(sql)

        async def fetchall(self):
            return [("conversation_id",), ("workflow_stage",)]

    cursor = FakeCursor()

    asyncio.run(ensure_conversation_states_compat(cursor))

    assert cursor.executed == ["SHOW COLUMNS FROM conversation_states"]


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
