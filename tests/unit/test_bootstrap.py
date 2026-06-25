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
            "007_add_worker_lease_fields.sql",
            "008_graph_run_errors.sql",
            "009_conversation_messages.sql",
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
    assert "KEY idx_external_commands_status_lease_created (status, lease_expires_at, created_at)" in sql
    assert "KEY idx_external_commands_locked_by (locked_by)" in sql
    assert "KEY idx_external_commands_conversation (conversation_id)" in sql
    assert "KEY idx_external_commands_inbound_event (inbound_event_id)" in sql


def test_external_command_results_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/006_external_command_results.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS external_command_results" in sql
    assert "UNIQUE KEY uk_external_command_results_dedup (dedup_key)" in sql
    assert "KEY idx_external_command_results_status_created (status, created_at)" in sql
    assert "KEY idx_external_command_results_status_lease_created (status, lease_expires_at, created_at)" in sql
    assert "KEY idx_external_command_results_locked_by (locked_by)" in sql
    assert "KEY idx_external_command_results_external_command (external_command_id)" in sql
    assert "KEY idx_external_command_results_conversation (conversation_id)" in sql
    assert "KEY idx_external_command_results_inbound_event (inbound_event_id)" in sql


def test_graph_run_errors_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/008_graph_run_errors.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS graph_run_errors" in sql
    assert "state_snapshot JSON NULL" in sql
    assert "KEY idx_graph_run_errors_conversation_created (conversation_id, created_at)" in sql
    assert "KEY idx_graph_run_errors_inbound_event (inbound_event_id)" in sql
    assert "KEY idx_graph_run_errors_retryable (retryable, created_at)" in sql


def test_conversation_messages_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/009_conversation_messages.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS conversation_messages" in sql
    assert "attachment_refs JSON NULL" in sql
    assert "UNIQUE KEY uk_conversation_messages_inbound" in sql
    assert "UNIQUE KEY uk_conversation_messages_outbound" in sql
    assert "UNIQUE KEY uk_conversation_messages_external_result" in sql
    assert "KEY idx_conversation_messages_conversation_created" in sql
    assert "KEY idx_conversation_messages_chat_thread_created" in sql


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


def test_external_command_lease_bootstrap_adds_missing_mysql_columns_and_indexes():
    import asyncio

    from app.db.bootstrap import ensure_external_command_lease_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []
            self.mode = "columns"

        async def execute(self, sql):
            self.executed.append(sql)
            self.mode = "indexes" if sql == "SHOW INDEX FROM external_commands" else self.mode

        async def fetchall(self):
            if self.mode == "indexes":
                return [("external_commands", 0, "idx_external_commands_status_created")]
            return [("id",), ("status",), ("created_at",)]

    cursor = FakeCursor()

    asyncio.run(ensure_external_command_lease_compat(cursor))

    assert "ALTER TABLE external_commands ADD COLUMN leased_at DATETIME(6) NULL" in cursor.executed
    assert "ALTER TABLE external_commands ADD COLUMN processed_at DATETIME(6) NULL" in cursor.executed
    assert any("idx_external_commands_status_lease_created" in sql for sql in cursor.executed)
    assert any("idx_external_commands_locked_by" in sql for sql in cursor.executed)


def test_external_result_lease_bootstrap_does_not_duplicate_existing_sqlite_fields_or_indexes():
    import asyncio

    from app.db.bootstrap import ensure_external_command_result_lease_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []
            self.phase = "columns"

        async def execute(self, sql):
            self.executed.append(sql)
            if sql.startswith("SHOW"):
                raise RuntimeError("sqlite")
            if sql.startswith("PRAGMA index_list"):
                self.phase = "indexes"

        async def fetchall(self):
            if self.phase == "indexes":
                return [
                    (0, "idx_external_command_results_status_lease_created"),
                    (1, "idx_external_command_results_locked_by"),
                ]
            return [
                (0, "leased_at"),
                (1, "lease_expires_at"),
                (2, "locked_by"),
                (3, "attempted_at"),
                (4, "retry_count"),
            ]

    cursor = FakeCursor()

    asyncio.run(ensure_external_command_result_lease_compat(cursor))

    assert not any(sql.startswith("ALTER TABLE external_command_results ADD COLUMN") for sql in cursor.executed)
    assert not any(sql.startswith("CREATE INDEX") for sql in cursor.executed)


def test_graph_run_errors_bootstrap_adds_missing_mysql_indexes_only_once():
    import asyncio

    from app.db.bootstrap import ensure_graph_run_errors_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []
            self.phase = "columns"

        async def execute(self, sql):
            self.executed.append(sql)
            if sql == "SHOW INDEX FROM graph_run_errors":
                self.phase = "indexes"

        async def fetchall(self):
            if self.phase == "indexes":
                return [("graph_run_errors", 0, "idx_graph_run_errors_inbound_event")]
            return [("id",), ("conversation_id",), ("inbound_event_id",), ("retryable",), ("created_at",)]

    cursor = FakeCursor()

    asyncio.run(ensure_graph_run_errors_compat(cursor))

    assert any("idx_graph_run_errors_conversation_created" in sql for sql in cursor.executed)
    assert any("idx_graph_run_errors_retryable" in sql for sql in cursor.executed)
    assert not any(sql == "ALTER TABLE graph_run_errors ADD COLUMN retryable TINYINT(1) NOT NULL DEFAULT 0" for sql in cursor.executed)


def test_graph_run_errors_bootstrap_keeps_existing_sqlite_columns_and_indexes():
    import asyncio

    from app.db.bootstrap import ensure_graph_run_errors_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []
            self.phase = "columns"

        async def execute(self, sql):
            self.executed.append(sql)
            if sql.startswith("SHOW"):
                raise RuntimeError("sqlite")
            if sql.startswith("PRAGMA index_list"):
                self.phase = "indexes"

        async def fetchall(self):
            if self.phase == "indexes":
                return [
                    (0, "idx_graph_run_errors_conversation_created"),
                    (1, "idx_graph_run_errors_inbound_event"),
                    (2, "idx_graph_run_errors_retryable"),
                ]
            return [
                (0, "conversation_id"),
                (1, "inbound_event_id"),
                (2, "graph_thread_id"),
                (3, "node_name"),
                (4, "error_type"),
                (5, "error_message"),
                (6, "retryable"),
                (7, "state_snapshot"),
                (8, "created_at"),
            ]

    cursor = FakeCursor()

    asyncio.run(ensure_graph_run_errors_compat(cursor))

    assert not any(sql.startswith("ALTER TABLE graph_run_errors ADD COLUMN") for sql in cursor.executed)
    assert not any(sql.startswith("CREATE INDEX") for sql in cursor.executed)


def test_conversation_messages_bootstrap_adds_missing_mysql_indexes_only_once():
    import asyncio

    from app.db.bootstrap import ensure_conversation_messages_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []
            self.phase = "columns"

        async def execute(self, sql):
            self.executed.append(sql)
            if sql == "SHOW INDEX FROM conversation_messages":
                self.phase = "indexes"

        async def fetchall(self):
            if self.phase == "indexes":
                return [("conversation_messages", 0, "uk_conversation_messages_inbound")]
            return [
                ("id",),
                ("conversation_id",),
                ("tenant_id",),
                ("channel_type",),
                ("inbound_event_id",),
                ("sender_role",),
                ("message_type",),
                ("attachment_refs",),
                ("source",),
                ("created_at",),
            ]

    cursor = FakeCursor()

    asyncio.run(ensure_conversation_messages_compat(cursor))

    assert any("uk_conversation_messages_outbound" in sql for sql in cursor.executed)
    assert any("uk_conversation_messages_external_result" in sql for sql in cursor.executed)
    assert any("idx_conversation_messages_conversation_created" in sql for sql in cursor.executed)
    assert any("idx_conversation_messages_chat_thread_created" in sql for sql in cursor.executed)
    assert not any(sql == "ALTER TABLE conversation_messages ADD COLUMN attachment_refs JSON NULL" for sql in cursor.executed)


def test_conversation_messages_bootstrap_keeps_existing_sqlite_columns_and_indexes():
    import asyncio

    from app.db.bootstrap import ensure_conversation_messages_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.executed = []
            self.phase = "columns"

        async def execute(self, sql):
            self.executed.append(sql)
            if sql.startswith("SHOW"):
                raise RuntimeError("sqlite")
            if sql.startswith("PRAGMA index_list"):
                self.phase = "indexes"

        async def fetchall(self):
            if self.phase == "indexes":
                return [
                    (0, "uk_conversation_messages_inbound"),
                    (1, "uk_conversation_messages_outbound"),
                    (2, "uk_conversation_messages_external_result"),
                    (3, "idx_conversation_messages_conversation_created"),
                    (4, "idx_conversation_messages_chat_thread_created"),
                ]
            return [
                (0, "conversation_id"),
                (1, "tenant_id"),
                (2, "channel_type"),
                (3, "chat_id"),
                (4, "thread_id"),
                (5, "inbound_event_id"),
                (6, "outbound_message_id"),
                (7, "external_command_result_id"),
                (8, "sender_role"),
                (9, "message_type"),
                (10, "text_content"),
                (11, "attachment_refs"),
                (12, "source"),
                (13, "occurred_at"),
                (14, "created_at"),
            ]

    cursor = FakeCursor()

    asyncio.run(ensure_conversation_messages_compat(cursor))

    assert not any(sql.startswith("ALTER TABLE conversation_messages ADD COLUMN") for sql in cursor.executed)
    assert not any(sql.startswith("CREATE INDEX") for sql in cursor.executed)
    assert not any("CREATE UNIQUE INDEX" in sql for sql in cursor.executed)
