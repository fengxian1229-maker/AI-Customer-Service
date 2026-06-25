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


async def fetch_columns(cur, table_name: str) -> set[str]:
    try:
        await cur.execute(f"SHOW COLUMNS FROM {table_name}")
        return {row[0] for row in await cur.fetchall()}
    except Exception:
        await cur.execute(f"PRAGMA table_info({table_name})")
        rows = await cur.fetchall()
        return {row[1] for row in rows}
