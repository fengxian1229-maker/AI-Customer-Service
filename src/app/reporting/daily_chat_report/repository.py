import json
from datetime import datetime
from typing import Any

import aiomysql


class DailyChatReportReadRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def fetch_messages(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        sql = """
        SELECT cm.id, cm.conversation_id, cm.chat_id, cm.thread_id, cm.sender_role, cm.message_type,
               cm.text_content, cm.attachment_refs, cm.source, cm.occurred_at, cm.created_at,
               ie.author_id,
               om.payload_json AS outbound_payload_json
        FROM conversation_messages cm
        LEFT JOIN inbound_events ie ON ie.id = cm.inbound_event_id
        LEFT JOIN outbound_messages om ON om.id = cm.outbound_message_id
        WHERE COALESCE(cm.occurred_at, cm.created_at) >= %s
          AND COALESCE(cm.occurred_at, cm.created_at) < %s
          AND cm.channel_type = 'livechat'
        ORDER BY COALESCE(cm.occurred_at, cm.created_at) ASC, cm.id ASC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (start_at, end_at))
                rows = await cur.fetchall()
        for row in rows:
            row["attachment_refs"] = _json_loads(row.get("attachment_refs")) or []
            row["speaker_name"] = _speaker_name_from_outbound_payload(row.get("outbound_payload_json"))
        return list(rows)

    async def fetch_metadata(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        sql = """
        SELECT chat_id, thread_id, payload_json
        FROM inbound_events
        WHERE occurred_at >= %s
          AND occurred_at < %s
          AND chat_id IS NOT NULL
        ORDER BY occurred_at ASC, id ASC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (start_at, end_at))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = _json_loads(row.get("payload_json")) or {}
        return list(rows)

    async def fetch_handoff_commands(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        sql = """
        SELECT chat_id, thread_id, command_type, payload_json, created_at
        FROM external_commands
        WHERE created_at >= %s
          AND created_at < %s
          AND chat_id IS NOT NULL
        ORDER BY created_at ASC, id ASC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (start_at, end_at))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = _json_loads(row.get("payload_json")) or {}
        return list(rows)

    async def fetch_states(self, updated_since: datetime, updated_before: datetime) -> list[dict[str, Any]]:
        sql = """
        SELECT chat_id, current_thread_id AS thread_id, status, active_workflow, workflow_stage, slot_memory
        FROM conversation_states
        WHERE updated_at >= %s
          AND updated_at < %s
          AND chat_id IS NOT NULL
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (updated_since, updated_before))
                rows = await cur.fetchall()
        for row in rows:
            row["slot_memory"] = _json_loads(row.get("slot_memory")) or {}
        return list(rows)


class LingxiDailyChatReportReadRepository:
    def __init__(self, pool, *, database: str = "lingxi_qa") -> None:
        self.pool = pool
        self.database = _quote_identifier(database)

    async def fetch_messages(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        sql = f"""
        SELECT dm.id,
               c.id AS conversation_id,
               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(c.metadata, '$.chat_id')), c.id) AS chat_id,
               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(c.metadata, '$.thread_id')), c.name, dm.dialogue_id) AS thread_id,
               CASE
                 WHEN dm.sender_type = 2 THEN 'agent'
                 WHEN dm.sender_type = 3 THEN 'system'
                 ELSE 'customer'
               END AS sender_role,
               CASE WHEN dm.sender_type = 2 THEN dm.sender ELSE NULL END AS speaker_name,
               dm.sender AS author_id,
               COALESCE(dm.content_type, 'text') AS message_type,
               dm.content AS text_content,
               '[]' AS attachment_refs,
               'lingxi_dialogue_messages2' AS source,
               dm.timestamp AS occurred_at,
               dm.timestamp AS created_at
        FROM {self.database}.dialogue_messages2 dm
        INNER JOIN {self.database}.conversations c ON c.id = dm.conversation_id
        WHERE c.started_at >= %s
          AND c.started_at < %s
        ORDER BY dm.timestamp ASC, dm.id ASC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (start_at, end_at))
                rows = await cur.fetchall()
        for row in rows:
            row["attachment_refs"] = []
        return list(rows)

    async def fetch_metadata(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        sql = f"""
        SELECT c.id AS conversation_id,
               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(c.metadata, '$.chat_id')), c.id) AS chat_id,
               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(c.metadata, '$.thread_id')), c.name) AS thread_id,
               c.project_id,
               c.user,
               c.agents,
               c.last_agent_name,
               c.metadata,
               p.name AS project_name,
               p.timezone AS project_timezone,
               b.group_ids
        FROM {self.database}.conversations c
        LEFT JOIN {self.database}.projects p ON p.id = c.project_id
        LEFT JOIN {self.database}.project_livechat_bindings b ON b.project_id = c.project_id
        WHERE c.started_at >= %s
          AND c.started_at < %s
        ORDER BY c.started_at ASC, c.id ASC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (start_at, end_at))
                rows = await cur.fetchall()
        return [_lingxi_metadata_row(row) for row in rows]

    async def fetch_handoff_commands(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        return []

    async def fetch_states(self, updated_since: datetime, updated_before: datetime) -> list[dict[str, Any]]:
        return []


class DailyChatReportAuditRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def ensure_table(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS daily_chat_reports (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          report_date DATE NOT NULL,
          target_chat_id VARCHAR(128) NOT NULL,
          message_thread_id BIGINT NOT NULL DEFAULT 0,
          status VARCHAR(64) NOT NULL,
          pdf_path VARCHAR(1024) NULL,
          telegram_message_id BIGINT NULL,
          error_message TEXT NULL,
          created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_daily_chat_reports_target (report_date, target_chat_id, message_thread_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, ())

    async def start_once(
        self,
        *,
        report_date: str,
        target_chat_id: str,
        message_thread_id: int | None,
        pdf_path: str,
    ) -> dict[str, Any]:
        sql = """
        INSERT IGNORE INTO daily_chat_reports (
          report_date, target_chat_id, message_thread_id, status, pdf_path
        ) VALUES (%s, %s, %s, 'STARTED', %s)
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (report_date, target_chat_id, _thread_key(message_thread_id), pdf_path))
                started = cur.rowcount == 1
                return {"started": started, "duplicate": not started, "id": cur.lastrowid if started else None}

    async def mark_sent(self, *, report_date: str, target_chat_id: str, message_thread_id: int | None, telegram_message_id: int | None) -> None:
        await self._update_status(report_date, target_chat_id, message_thread_id, "SENT", telegram_message_id, None)

    async def mark_failed(self, *, report_date: str, target_chat_id: str, message_thread_id: int | None, error_message: str) -> None:
        await self._update_status(report_date, target_chat_id, message_thread_id, "FAILED", None, error_message[:2000])

    async def _update_status(
        self,
        report_date: str,
        target_chat_id: str,
        message_thread_id: int | None,
        status: str,
        telegram_message_id: int | None,
        error_message: str | None,
    ) -> None:
        sql = """
        UPDATE daily_chat_reports
        SET status = %s, telegram_message_id = %s, error_message = %s
        WHERE report_date = %s
          AND target_chat_id = %s
          AND message_thread_id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, telegram_message_id, error_message, report_date, target_chat_id, _thread_key(message_thread_id)))


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _speaker_name_from_outbound_payload(value: Any) -> str | None:
    payload = _json_loads(value) or {}
    for key in ("sender_name", "author_name", "agent_name", "bot_name"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return None


def _lingxi_metadata_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _json_loads(row.get("metadata")) or {}
    group_ids = _json_loads(row.get("group_ids")) or []
    group_id = _first_group_id(group_ids)
    agents = _json_loads(row.get("agents")) or []
    chat_users = []
    user = str(row.get("user") or "").strip()
    if user:
        chat_users.append({"id": user, "type": "customer", "name": user})
    for agent in agents:
        name = str(agent or "").strip()
        if name:
            chat_users.append({"id": name, "type": "agent", "name": name})
    last_agent_name = str(row.get("last_agent_name") or "").strip()
    if last_agent_name and all(user_item.get("id") != last_agent_name for user_item in chat_users):
        chat_users.append({"id": last_agent_name, "type": "agent", "name": last_agent_name})
    payload = {
        "livechat_group_id": group_id,
        "group_ids": group_ids,
        "lingxi_agent_names": [item["name"] for item in chat_users if item.get("type") == "agent"],
        "lingxi_agent_participated": bool(agents or last_agent_name),
        "platform": _platform_label(row.get("project_name"), group_id),
        "project_id": row.get("project_id"),
        "project_name": row.get("project_name"),
        "chat_users": chat_users,
        "last_thread_summary": {
            "customer_name": user,
            "agent_name": last_agent_name,
        },
        **metadata,
    }
    return {
        "chat_id": row.get("chat_id"),
        "thread_id": row.get("thread_id"),
        "payload_json": payload,
    }


def _first_group_id(group_ids: list[Any]) -> int | None:
    for value in group_ids:
        if str(value).strip().isdigit():
            return int(value)
    return None


def _platform_label(project_name: Any, group_id: int | None) -> str | None:
    text = str(project_name or "").strip()
    if text:
        return text
    if group_id is not None:
        return f"group-{group_id}"
    return None


def _quote_identifier(value: str) -> str:
    safe = str(value or "").replace("`", "")
    if not safe:
        raise ValueError("database name is required")
    return f"`{safe}`"


def _thread_key(message_thread_id: int | None) -> int:
    return int(message_thread_id or 0)
