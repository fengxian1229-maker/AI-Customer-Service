from pathlib import Path


def load_sql_files(sql_dir: Path) -> list[Path]:
    return sorted(sql_dir.glob("*.sql"))


async def bootstrap_database(pool, sql_dir: Path) -> None:
    sql_files = load_sql_files(sql_dir)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for path in sql_files:
                if path.name == "004_add_workflow_stage.sql":
                    continue
                statement = path.read_text(encoding="utf-8")
                await cur.execute(statement)
            await ensure_inbound_events_compat(cur)
            await ensure_conversation_states_compat(cur)
            await ensure_outbound_messages_compat(cur)
            await ensure_external_command_lease_compat(cur)
            await ensure_external_command_result_lease_compat(cur)
            await ensure_graph_run_errors_compat(cur)
            await ensure_conversation_messages_compat(cur)
            await ensure_knowledge_documents_compat(cur)


async def ensure_inbound_events_compat(cur) -> None:
    await cur.execute("SHOW COLUMNS FROM inbound_events")
    columns = {row[0] for row in await cur.fetchall()}
    additions = {
        "organization_id": "ALTER TABLE inbound_events ADD COLUMN organization_id VARCHAR(128) NULL",
        "standard_event_type": "ALTER TABLE inbound_events ADD COLUMN standard_event_type VARCHAR(64) NOT NULL DEFAULT 'UNSUPPORTED'",
        "author_id": "ALTER TABLE inbound_events ADD COLUMN author_id VARCHAR(128) NULL",
        "ignored": "ALTER TABLE inbound_events ADD COLUMN ignored TINYINT(1) NOT NULL DEFAULT 0",
        "ignore_reason": "ALTER TABLE inbound_events ADD COLUMN ignore_reason VARCHAR(128) NULL",
        "processed": "ALTER TABLE inbound_events ADD COLUMN processed TINYINT(1) NOT NULL DEFAULT 0",
        "updated_at": "ALTER TABLE inbound_events ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    }
    for column, statement in additions.items():
        if column not in columns:
            await cur.execute(statement)
    if "account_id" in columns:
        await cur.execute("ALTER TABLE inbound_events MODIFY account_id VARCHAR(128) NULL")
    if "created_at" in columns:
        await cur.execute("ALTER TABLE inbound_events MODIFY created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)")


async def ensure_outbound_messages_compat(cur) -> None:
    await cur.execute("SHOW INDEX FROM outbound_messages WHERE Key_name = 'uk_inbound_action'")
    existing = await cur.fetchall()
    if not existing:
        await cur.execute("ALTER TABLE outbound_messages ADD UNIQUE KEY uk_inbound_action (inbound_event_id, action_type)")


async def ensure_conversation_states_compat(cur) -> None:
    columns = await fetch_columns(cur, "conversation_states")
    if "workflow_stage" not in columns:
        await cur.execute("ALTER TABLE conversation_states ADD COLUMN workflow_stage VARCHAR(128) NULL")


async def ensure_external_command_lease_compat(cur) -> None:
    await ensure_columns(
        cur,
        "external_commands",
        {
            "leased_at": "ALTER TABLE external_commands ADD COLUMN leased_at DATETIME(6) NULL",
            "lease_expires_at": "ALTER TABLE external_commands ADD COLUMN lease_expires_at DATETIME(6) NULL",
            "locked_by": "ALTER TABLE external_commands ADD COLUMN locked_by VARCHAR(128) NULL",
            "attempted_at": "ALTER TABLE external_commands ADD COLUMN attempted_at DATETIME(6) NULL",
            "processed_at": "ALTER TABLE external_commands ADD COLUMN processed_at DATETIME(6) NULL",
        },
    )
    await ensure_indexes(
        cur,
        "external_commands",
        {
            "idx_external_commands_status_lease_created": (
                "CREATE INDEX idx_external_commands_status_lease_created "
                "ON external_commands (status, lease_expires_at, created_at)"
            ),
            "idx_external_commands_locked_by": (
                "CREATE INDEX idx_external_commands_locked_by ON external_commands (locked_by)"
            ),
        },
    )


async def ensure_external_command_result_lease_compat(cur) -> None:
    await ensure_columns(
        cur,
        "external_command_results",
        {
            "leased_at": "ALTER TABLE external_command_results ADD COLUMN leased_at DATETIME(6) NULL",
            "lease_expires_at": "ALTER TABLE external_command_results ADD COLUMN lease_expires_at DATETIME(6) NULL",
            "locked_by": "ALTER TABLE external_command_results ADD COLUMN locked_by VARCHAR(128) NULL",
            "attempted_at": "ALTER TABLE external_command_results ADD COLUMN attempted_at DATETIME(6) NULL",
            "retry_count": "ALTER TABLE external_command_results ADD COLUMN retry_count INT NOT NULL DEFAULT 0",
        },
    )
    await ensure_indexes(
        cur,
        "external_command_results",
        {
            "idx_external_command_results_status_lease_created": (
                "CREATE INDEX idx_external_command_results_status_lease_created "
                "ON external_command_results (status, lease_expires_at, created_at)"
            ),
            "idx_external_command_results_locked_by": (
                "CREATE INDEX idx_external_command_results_locked_by ON external_command_results (locked_by)"
            ),
        },
    )


async def ensure_graph_run_errors_compat(cur) -> None:
    await ensure_columns(
        cur,
        "graph_run_errors",
        {
            "graph_thread_id": "ALTER TABLE graph_run_errors ADD COLUMN graph_thread_id VARCHAR(128) NULL",
            "node_name": "ALTER TABLE graph_run_errors ADD COLUMN node_name VARCHAR(128) NULL",
            "retryable": "ALTER TABLE graph_run_errors ADD COLUMN retryable TINYINT(1) NOT NULL DEFAULT 0",
            "state_snapshot": "ALTER TABLE graph_run_errors ADD COLUMN state_snapshot JSON NULL",
            "created_at": (
                "ALTER TABLE graph_run_errors "
                "ADD COLUMN created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
            ),
        },
    )
    await ensure_indexes(
        cur,
        "graph_run_errors",
        {
            "idx_graph_run_errors_conversation_created": (
                "CREATE INDEX idx_graph_run_errors_conversation_created "
                "ON graph_run_errors (conversation_id, created_at)"
            ),
            "idx_graph_run_errors_inbound_event": (
                "CREATE INDEX idx_graph_run_errors_inbound_event ON graph_run_errors (inbound_event_id)"
            ),
            "idx_graph_run_errors_retryable": (
                "CREATE INDEX idx_graph_run_errors_retryable ON graph_run_errors (retryable, created_at)"
            ),
        },
    )


async def ensure_conversation_messages_compat(cur) -> None:
    await ensure_columns(
        cur,
        "conversation_messages",
        {
            "tenant_id": "ALTER TABLE conversation_messages ADD COLUMN tenant_id VARCHAR(128) NOT NULL DEFAULT 'default'",
            "channel_type": "ALTER TABLE conversation_messages ADD COLUMN channel_type VARCHAR(64) NOT NULL DEFAULT 'livechat'",
            "chat_id": "ALTER TABLE conversation_messages ADD COLUMN chat_id VARCHAR(128) NULL",
            "thread_id": "ALTER TABLE conversation_messages ADD COLUMN thread_id VARCHAR(128) NULL",
            "outbound_message_id": "ALTER TABLE conversation_messages ADD COLUMN outbound_message_id BIGINT UNSIGNED NULL",
            "external_command_result_id": "ALTER TABLE conversation_messages ADD COLUMN external_command_result_id BIGINT UNSIGNED NULL",
            "text_content": "ALTER TABLE conversation_messages ADD COLUMN text_content TEXT NULL",
            "attachment_refs": "ALTER TABLE conversation_messages ADD COLUMN attachment_refs JSON NULL",
            "source": "ALTER TABLE conversation_messages ADD COLUMN source VARCHAR(64) NOT NULL DEFAULT 'inbound_event'",
            "occurred_at": "ALTER TABLE conversation_messages ADD COLUMN occurred_at DATETIME(6) NULL",
            "created_at": (
                "ALTER TABLE conversation_messages "
                "ADD COLUMN created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
            ),
        },
    )
    await ensure_indexes(
        cur,
        "conversation_messages",
        {
            "uk_conversation_messages_inbound": (
                "CREATE UNIQUE INDEX uk_conversation_messages_inbound "
                "ON conversation_messages (inbound_event_id, sender_role, message_type)"
            ),
            "uk_conversation_messages_outbound": (
                "CREATE UNIQUE INDEX uk_conversation_messages_outbound "
                "ON conversation_messages (outbound_message_id)"
            ),
            "uk_conversation_messages_external_result": (
                "CREATE UNIQUE INDEX uk_conversation_messages_external_result "
                "ON conversation_messages (external_command_result_id, sender_role, message_type)"
            ),
            "idx_conversation_messages_conversation_created": (
                "CREATE INDEX idx_conversation_messages_conversation_created "
                "ON conversation_messages (conversation_id, created_at, id)"
            ),
            "idx_conversation_messages_chat_thread_created": (
                "CREATE INDEX idx_conversation_messages_chat_thread_created "
                "ON conversation_messages (chat_id, thread_id, created_at)"
            ),
        },
    )


async def ensure_knowledge_documents_compat(cur) -> None:
    await ensure_columns(
        cur,
        "knowledge_documents",
        {
            "tenant_id": "ALTER TABLE knowledge_documents ADD COLUMN tenant_id VARCHAR(128) NOT NULL DEFAULT 'default'",
            "kb_scope": "ALTER TABLE knowledge_documents ADD COLUMN kb_scope VARCHAR(128) NOT NULL DEFAULT 'default'",
            "title": "ALTER TABLE knowledge_documents ADD COLUMN title VARCHAR(255) NOT NULL",
            "content": "ALTER TABLE knowledge_documents ADD COLUMN content TEXT NOT NULL",
            "keywords": "ALTER TABLE knowledge_documents ADD COLUMN keywords JSON NULL",
            "language": "ALTER TABLE knowledge_documents ADD COLUMN language VARCHAR(32) NULL",
            "priority": "ALTER TABLE knowledge_documents ADD COLUMN priority INT NOT NULL DEFAULT 100",
            "enabled": "ALTER TABLE knowledge_documents ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1",
            "created_at": (
                "ALTER TABLE knowledge_documents "
                "ADD COLUMN created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
            ),
            "updated_at": (
                "ALTER TABLE knowledge_documents "
                "ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
            ),
        },
    )
    await ensure_indexes(
        cur,
        "knowledge_documents",
        {
            "uk_knowledge_documents_tenant_scope_title": (
                "CREATE UNIQUE INDEX uk_knowledge_documents_tenant_scope_title "
                "ON knowledge_documents (tenant_id, kb_scope, title)"
            ),
            "idx_knowledge_documents_tenant_enabled_priority": (
                "CREATE INDEX idx_knowledge_documents_tenant_enabled_priority "
                "ON knowledge_documents (tenant_id, enabled, priority, id)"
            ),
            "idx_knowledge_documents_scope": (
                "CREATE INDEX idx_knowledge_documents_scope "
                "ON knowledge_documents (tenant_id, kb_scope, enabled)"
            ),
        },
    )


async def ensure_columns(cur, table_name: str, additions: dict[str, str]) -> None:
    columns = await fetch_columns(cur, table_name)
    for column, statement in additions.items():
        if column not in columns:
            await cur.execute(statement)


async def ensure_indexes(cur, table_name: str, additions: dict[str, str]) -> None:
    indexes = await fetch_indexes(cur, table_name)
    for index, statement in additions.items():
        if index not in indexes:
            await cur.execute(statement)


async def fetch_columns(cur, table_name: str) -> set[str]:
    try:
        await cur.execute(f"SHOW COLUMNS FROM {table_name}")
        return {row[0] for row in await cur.fetchall()}
    except Exception:
        await cur.execute(f"PRAGMA table_info({table_name})")
        rows = await cur.fetchall()
        return {row[1] for row in rows}


async def fetch_indexes(cur, table_name: str) -> set[str]:
    try:
        await cur.execute(f"SHOW INDEX FROM {table_name}")
        return {row[2] for row in await cur.fetchall()}
    except Exception:
        await cur.execute(f"PRAGMA index_list({table_name})")
        rows = await cur.fetchall()
        return {row[1] for row in rows}
