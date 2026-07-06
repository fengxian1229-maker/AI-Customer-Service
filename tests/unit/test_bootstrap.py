from app.core.settings import Settings
import pytest


def test_settings_defaults():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
    )

    assert settings.livechat_api_base == "https://api.livechatinc.com/v3.6"
    assert settings.poll_seconds == 5
    assert settings.mysql_port == 3306
    assert settings.langgraph_checkpoint_setup_on_start is False
    assert settings.livechat_webhook_secret is None
    assert settings.livechat_webhook_enabled is False
    assert settings.webhook_server_host == "0.0.0.0"
    assert settings.webhook_server_port == 8000


def test_settings_livechat_handoff_target_group_id_accepts_blank_as_none():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_target_group_id=" ",
    )

    assert settings.livechat_handoff_target_group_id is None


def test_settings_livechat_handoff_target_group_id_parses_positive_string():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        livechat_handoff_target_group_id="23",
    )

    assert settings.livechat_handoff_target_group_id == 23


@pytest.mark.parametrize("value", ["abc", "0", -1])
def test_settings_livechat_handoff_target_group_id_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        Settings(
            livechat_agent_access_token="token",
            livechat_account_id="account",
            livechat_handoff_target_group_id=value,
        )


def test_settings_mysql_checkpoint_dsn_url_encodes_password():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        mysql_user="user",
        mysql_password="p@ss word",
        mysql_host="db.internal",
        mysql_port=3307,
        mysql_database="livechat_ai",
    )

    assert settings.mysql_checkpoint_dsn == "mysql://user:p%40ss+word@db.internal:3307/livechat_ai?charset=utf8mb4"


def test_build_app_has_health_route():
    from app.api.main import build_app

    app = build_app()

    def route_paths(routes):
        paths = set()
        for route in routes:
            if hasattr(route, "path"):
                paths.add(route.path)
            if hasattr(route, "routes"):
                paths.update(route_paths(route.routes))
            if hasattr(route, "original_router"):
                paths.update(route_paths(route.original_router.routes))
        return paths

    paths = route_paths(app.routes)
    assert "/healthz" in paths
    assert "/api/v1/webhooks/livechat" in paths


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
        "010_knowledge_documents.sql",
        "011_graph_checkpoint_metadata.sql",
        "012_add_multimodal_knowledge_fields.sql",
        "013_add_outbound_message_dedup_fields.sql",
        "014_telegram_cases.sql",
        "015_telegram_case_messages.sql",
        "016_telegram_update_offsets.sql",
    ]


def test_outbound_messages_schema_does_not_keep_legacy_inbound_action_unique_key():
    from pathlib import Path

    sql = Path("sql/003_outbound_messages.sql").read_text()

    assert "UNIQUE KEY uk_inbound_action" not in sql


def test_outbound_messages_schema_has_multiblock_dedup_fields():
    from pathlib import Path

    sql = Path("sql/003_outbound_messages.sql").read_text()
    migration = Path("sql/013_add_outbound_message_dedup_fields.sql").read_text()

    for ddl in (sql, migration):
        assert "dedup_key VARCHAR(255) NULL" in ddl
        assert "block_index INT NULL" in ddl
        assert "message_kind VARCHAR(64) NULL" in ddl
        assert "command_type VARCHAR(128) NULL" in ddl
    assert "UNIQUE KEY uk_outbound_messages_dedup_key (dedup_key)" in sql
    assert "uk_outbound_messages_dedup_key" in migration


def test_telegram_cases_schema_has_reply_lookup_tables():
    from pathlib import Path

    cases_sql = Path("sql/014_telegram_cases.sql").read_text()
    messages_sql = Path("sql/015_telegram_case_messages.sql").read_text()
    offsets_sql = Path("sql/016_telegram_update_offsets.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS telegram_cases" in cases_sql
    assert "CREATE TABLE IF NOT EXISTS telegram_case_messages" in messages_sql
    assert "CREATE TABLE IF NOT EXISTS telegram_update_offsets" in offsets_sql
    assert "UNIQUE KEY uk_telegram_cases_target_root" in cases_sql
    assert "UNIQUE KEY uk_telegram_case_messages_chat_message" in messages_sql


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


def test_knowledge_documents_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/010_knowledge_documents.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS knowledge_documents" in sql
    assert "tenant_id VARCHAR(128) NOT NULL DEFAULT 'default'" in sql
    assert "kb_scope VARCHAR(128) NOT NULL DEFAULT 'default'" in sql
    assert "title VARCHAR(255) NOT NULL" in sql
    assert "content TEXT NOT NULL" in sql
    assert "keywords JSON NULL" in sql
    assert "enabled TINYINT(1) NOT NULL DEFAULT 1" in sql
    assert "priority INT NOT NULL DEFAULT 100" in sql
    assert "UNIQUE KEY uk_knowledge_documents_tenant_scope_title" in sql
    assert "KEY idx_knowledge_documents_tenant_enabled_priority" in sql
    assert "KEY idx_knowledge_documents_scope" in sql


def test_multimodal_knowledge_fields_migration_adds_json_columns():
    from pathlib import Path

    sql = Path("sql/012_add_multimodal_knowledge_fields.sql").read_text()

    assert "question_aliases JSON NULL" in sql
    assert "answer_blocks JSON NULL" in sql
    assert "metadata_json JSON NULL" in sql


def test_graph_checkpoint_runs_schema_has_required_indexes():
    from pathlib import Path

    sql = Path("sql/011_graph_checkpoint_metadata.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS graph_checkpoint_runs" in sql
    assert "checkpoint_mode VARCHAR(32) NOT NULL" in sql
    assert "latest_checkpoint_id VARCHAR(255) NULL" in sql
    assert "metadata_json JSON NULL" in sql
    assert "KEY idx_graph_checkpoint_runs_conversation_created" in sql
    assert "KEY idx_graph_checkpoint_runs_thread_created" in sql
    assert "KEY idx_graph_checkpoint_runs_status_created" in sql
    assert "KEY idx_graph_checkpoint_runs_inbound_event" in sql


def test_readme_keeps_worker_operation_commands():
    from pathlib import Path

    readme = Path("README.md").read_text()

    assert "Poll LiveChat Once" in readme
    assert "Run Gateway Once" in readme
    assert "Run Sender Once" in readme
    assert "Safe Group 23 Smoke" in readme


def test_bootstrap_adds_missing_workflow_stage_for_mysql():
    import asyncio

    from app.db.bootstrap import ensure_conversation_states_compat

    class FakeCursor:
        def __init__(self) -> None:
            self.statements = []

        async def execute(self, sql):
            self.statements.append(sql)

        async def fetchall(self):
            if self.statements[-1] == "SHOW COLUMNS FROM conversation_states":
                return [("conversation_id",), ("status",)]
            return []

    cur = FakeCursor()

    asyncio.run(ensure_conversation_states_compat(cur))

    assert "ALTER TABLE conversation_states ADD COLUMN workflow_stage VARCHAR(128) NULL" in cur.statements
