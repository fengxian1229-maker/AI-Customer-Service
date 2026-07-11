import json
from datetime import datetime, timedelta
from typing import Any

import aiomysql

from app.channels.livechat.normalizer import parse_rfc3339_to_mysql
from app.channels.livechat.polling_receiver import chat_group_ids
from app.config.platforms import platform_for_livechat_group_id


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


class LingxiLiveChatReportReadRepository:
    def __init__(self, pool, *, self_author_ids: set[str]) -> None:
        self.pool = pool
        self.self_author_ids = {str(item).strip() for item in self_author_ids if str(item).strip()}

    async def fetch_messages(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        candidate_chat_ids = await self._fetch_candidate_chat_ids(start_at, end_at)
        if not candidate_chat_ids:
            return []
        rows = await self._fetch_conversation_messages(start_at, end_at, candidate_chat_ids=candidate_chat_ids)
        normalized_inbound_event_ids = {
            str(row.get("inbound_event_id"))
            for row in rows
            if row.get("inbound_event_id") is not None
        }
        normalized_outbound_message_ids = {
            str(row.get("outbound_message_id"))
            for row in rows
            if row.get("outbound_message_id") is not None
        }
        inbound_rows = await self._fetch_inbound_event_messages(start_at, end_at, candidate_chat_ids=candidate_chat_ids)
        rows.extend(
            row
            for row in inbound_rows
            if str(row.get("inbound_event_id")) not in normalized_inbound_event_ids
        )
        outbound_staff_rows = await self._fetch_outbound_staff_reply_messages(
            start_at,
            end_at + timedelta(days=1),
            candidate_chat_ids=candidate_chat_ids,
        )
        rows.extend(row for row in outbound_staff_rows if str(row.get("outbound_message_id")) not in normalized_outbound_message_ids)
        rows = _attach_followup_messages_to_lingxi_threads(rows)
        return sorted(
            rows,
            key=lambda row: (
                row.get("occurred_at") or row.get("created_at") or datetime.min,
                str(row.get("id") or ""),
            ),
        )

    async def _fetch_candidate_chat_ids(self, start_at: datetime, end_at: datetime) -> list[str]:
        candidates = await self._fetch_candidate_threads(start_at, end_at)
        return sorted({chat_id for chat_id, _thread_id in candidates})

    async def _fetch_candidate_threads(self, start_at: datetime, end_at: datetime) -> list[tuple[str, str | None]]:
        placeholders = self._self_author_placeholders()
        sql = f"""
        SELECT DISTINCT chat_id, thread_id
        FROM (
          SELECT cm.chat_id, cm.thread_id
          FROM conversation_messages cm
          WHERE COALESCE(cm.occurred_at, cm.created_at) >= %s
            AND COALESCE(cm.occurred_at, cm.created_at) < %s
            AND cm.channel_type = 'livechat'
            AND cm.sender_role = 'assistant'
            AND cm.chat_id IS NOT NULL
          UNION
          SELECT ie.chat_id, ie.thread_id
          FROM inbound_events ie
          WHERE ie.occurred_at >= %s
            AND ie.occurred_at < %s
            AND ie.author_id IN ({placeholders})
            AND ie.chat_id IS NOT NULL
        ) candidate_chats
        ORDER BY chat_id, thread_id
        """
        params = [start_at, end_at, start_at, end_at] + self._self_author_params()
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [
            (str(row.get("chat_id")), _optional_str(row.get("thread_id")))
            for row in rows
            if row.get("chat_id")
        ]

    async def fetch_metadata(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        placeholders = self._self_author_placeholders()
        sql = f"""
        SELECT ie.chat_id, ie.thread_id, ie.payload_json
        FROM inbound_events ie
        WHERE ie.occurred_at >= %s
          AND ie.occurred_at < %s
          AND ie.chat_id IS NOT NULL
          AND (
            ie.author_id IN ({placeholders})
            OR EXISTS (
              SELECT 1
              FROM inbound_events self_ie
              WHERE self_ie.occurred_at >= %s
                AND self_ie.occurred_at < %s
                AND self_ie.author_id IN ({placeholders})
                AND self_ie.chat_id = ie.chat_id
            )
            OR EXISTS (
              SELECT 1
              FROM conversation_messages cm
              WHERE COALESCE(cm.occurred_at, cm.created_at) >= %s
                AND COALESCE(cm.occurred_at, cm.created_at) < %s
                AND cm.channel_type = 'livechat'
                AND cm.sender_role = 'assistant'
                AND cm.chat_id = ie.chat_id
            )
          )
        ORDER BY ie.occurred_at ASC, ie.id ASC
        """
        params = (
            [start_at, end_at]
            + self._self_author_params()
            + [start_at, end_at]
            + self._self_author_params()
            + [start_at, end_at]
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = _json_loads(row.get("payload_json")) or {}
        return list(rows)

    async def fetch_handoff_commands(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        placeholders = self._self_author_placeholders()
        sql = f"""
        SELECT ec.chat_id, ec.thread_id, ec.command_type, ec.payload_json, ec.created_at
        FROM external_commands ec
        WHERE ec.created_at >= %s
          AND ec.created_at < %s
          AND ec.chat_id IS NOT NULL
          AND (
            EXISTS (
              SELECT 1
              FROM inbound_events self_ie
              WHERE self_ie.occurred_at >= %s
                AND self_ie.occurred_at < %s
                AND self_ie.author_id IN ({placeholders})
                AND self_ie.chat_id = ec.chat_id
                AND self_ie.thread_id <=> ec.thread_id
            )
            OR EXISTS (
              SELECT 1
              FROM conversation_messages cm
              WHERE COALESCE(cm.occurred_at, cm.created_at) >= %s
                AND COALESCE(cm.occurred_at, cm.created_at) < %s
                AND cm.channel_type = 'livechat'
                AND cm.sender_role = 'assistant'
                AND cm.chat_id = ec.chat_id
                AND cm.thread_id <=> ec.thread_id
            )
          )
        ORDER BY ec.created_at ASC, ec.id ASC
        """
        params = [start_at, end_at, start_at, end_at] + self._self_author_params() + [start_at, end_at]
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = _json_loads(row.get("payload_json")) or {}
        return list(rows)

    async def fetch_states(self, updated_since: datetime, updated_before: datetime) -> list[dict[str, Any]]:
        placeholders = self._self_author_placeholders()
        sql = f"""
        SELECT cs.chat_id, cs.current_thread_id AS thread_id, cs.status, cs.active_workflow, cs.workflow_stage, cs.slot_memory
        FROM conversation_states cs
        WHERE cs.chat_id IS NOT NULL
          AND (
            EXISTS (
              SELECT 1
              FROM inbound_events self_ie
              WHERE self_ie.occurred_at >= %s
                AND self_ie.occurred_at < %s
                AND self_ie.author_id IN ({placeholders})
                AND self_ie.chat_id = cs.chat_id
                AND self_ie.thread_id <=> cs.current_thread_id
            )
            OR EXISTS (
              SELECT 1
              FROM conversation_messages cm
              WHERE COALESCE(cm.occurred_at, cm.created_at) >= %s
                AND COALESCE(cm.occurred_at, cm.created_at) < %s
                AND cm.channel_type = 'livechat'
                AND cm.sender_role = 'assistant'
                AND cm.chat_id = cs.chat_id
                AND cm.thread_id <=> cs.current_thread_id
            )
          )
        """
        params = [updated_since, updated_before] + self._self_author_params() + [updated_since, updated_before]
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        for row in rows:
            row["slot_memory"] = _json_loads(row.get("slot_memory")) or {}
        return list(rows)

    async def _fetch_conversation_messages(self, start_at: datetime, end_at: datetime, *, candidate_chat_ids: list[str]) -> list[dict[str, Any]]:
        chat_placeholders = _placeholders(candidate_chat_ids)
        sql = f"""
        SELECT cm.id, cm.conversation_id, cm.chat_id, cm.thread_id, cm.sender_role, cm.message_type,
               cm.text_content, cm.attachment_refs, cm.source, cm.occurred_at, cm.created_at,
               cm.inbound_event_id, cm.outbound_message_id,
               ie.author_id,
               om.payload_json AS outbound_payload_json
        FROM conversation_messages cm
        LEFT JOIN inbound_events ie ON ie.id = cm.inbound_event_id
        LEFT JOIN outbound_messages om ON om.id = cm.outbound_message_id
        WHERE COALESCE(cm.occurred_at, cm.created_at) >= %s
          AND COALESCE(cm.occurred_at, cm.created_at) < %s
          AND cm.channel_type = 'livechat'
          AND cm.chat_id IN ({chat_placeholders})
        ORDER BY COALESCE(cm.occurred_at, cm.created_at) ASC, cm.id ASC
        """
        params = [start_at, end_at] + candidate_chat_ids
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        for row in rows:
            row["attachment_refs"] = _json_loads(row.get("attachment_refs")) or []
            row["speaker_name"] = _speaker_name_from_outbound_payload(row.get("outbound_payload_json"))
        return list(rows)

    async def _fetch_inbound_event_messages(self, start_at: datetime, end_at: datetime, *, candidate_chat_ids: list[str]) -> list[dict[str, Any]]:
        placeholders = self._self_author_placeholders()
        chat_placeholders = _placeholders(candidate_chat_ids)
        sql = f"""
        SELECT ie.id, ie.chat_id, ie.thread_id, ie.author_id, ie.sender_role,
               ie.event_type, ie.standard_event_type, ie.payload_json, ie.ignore_reason,
               ie.occurred_at, ie.created_at
        FROM inbound_events ie
        WHERE ie.occurred_at >= %s
          AND ie.occurred_at < %s
          AND ie.chat_id IS NOT NULL
          AND ie.chat_id IN ({chat_placeholders})
          AND (
            ie.author_id IN ({placeholders})
            OR ie.ignore_reason = 'agent_message'
            OR (ie.sender_role = 'external' AND ie.ignore_reason IS NULL)
          )
        ORDER BY ie.occurred_at ASC, ie.id ASC
        """
        params = (
            [start_at, end_at]
            + candidate_chat_ids
            + self._self_author_params()
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [self._message_from_inbound_event(row) for row in rows]

    async def _fetch_outbound_staff_reply_messages(self, start_at: datetime, end_at: datetime, *, candidate_chat_ids: list[str]) -> list[dict[str, Any]]:
        chat_placeholders = _placeholders(candidate_chat_ids)
        sql = f"""
        SELECT id, chat_id, thread_id, message_type, payload_json, sent_at, created_at, command_type, message_kind
        FROM outbound_messages
        WHERE COALESCE(sent_at, created_at) >= %s
          AND COALESCE(sent_at, created_at) < %s
          AND chat_id IN ({chat_placeholders})
          AND (
            command_type = 'telegram.staff_reply'
            OR message_kind = 'telegram_staff_reply'
          )
          AND status = 'SENT'
        ORDER BY COALESCE(sent_at, created_at) ASC, id ASC
        """
        params = [start_at, end_at] + candidate_chat_ids
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [_message_from_outbound_staff_reply(row) for row in rows]

    def _message_from_inbound_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = _json_loads(row.get("payload_json")) or {}
        author_id = str(row.get("author_id") or "")
        is_self = author_id in self.self_author_ids
        message_type = _message_type_from_inbound_payload(payload, row.get("event_type"))
        return {
            "id": f"inbound:{row.get('id')}",
            "inbound_event_id": row.get("id"),
            "conversation_id": f"livechat:{row.get('chat_id')}",
            "chat_id": row.get("chat_id"),
            "thread_id": row.get("thread_id"),
            "sender_role": _sender_role_from_inbound_event(row, is_self=is_self),
            "message_type": message_type,
            "text_content": _text_from_inbound_payload(payload),
            "attachment_refs": _attachment_refs_from_inbound_payload(payload),
            "source": _source_from_inbound_event(row, is_self=is_self),
            "occurred_at": row.get("occurred_at"),
            "created_at": row.get("created_at"),
            "author_id": row.get("author_id"),
            "speaker_name": _speaker_name_for_inbound_event(row, payload, author_id, is_self=is_self),
        }

    def _self_author_placeholders(self) -> str:
        if not self.self_author_ids:
            return "NULL"
        return ", ".join(["%s"] * len(self.self_author_ids))

    def _self_author_params(self) -> list[str]:
        return sorted(self.self_author_ids)


class LingxiLiveChatApiReportReadRepository(LingxiLiveChatReportReadRepository):
    def __init__(self, pool, *, livechat_client, self_author_ids: set[str]) -> None:
        super().__init__(pool, self_author_ids=self_author_ids)
        self.livechat_client = livechat_client

    async def fetch_messages(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        chats = await self._fetch_candidate_livechat_chats(start_at, end_at)
        rows = []
        for chat in chats:
            rows.extend(_message_rows_from_livechat_chat(chat, self_author_ids=self.self_author_ids))
        rows = [
            row
            for row in rows
            if start_at <= (row.get("occurred_at") or row.get("created_at") or datetime.min) < end_at + timedelta(days=1)
        ]
        return sorted(rows, key=_row_sort_key)

    async def fetch_metadata(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        chats = await self._fetch_candidate_livechat_chats(start_at, end_at)
        return [_metadata_row_from_livechat_chat(chat) for chat in chats]

    async def _fetch_candidate_livechat_chats(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        candidate_threads = await self._fetch_candidate_threads(start_at, end_at)
        chats = []
        seen = set()
        for chat_id, thread_id in candidate_threads:
            if thread_id:
                archives = await self.livechat_client.list_archives(filters={"chat_ids": [chat_id], "query": thread_id}, limit=1)
                for chat in archives.get("chats") or []:
                    archive_thread_id = ((chat.get("thread") or {}).get("id"))
                    key = (chat.get("id"), archive_thread_id)
                    if key not in seen:
                        seen.add(key)
                        chats.append(chat)
                continue
            if (chat_id, None) in seen:
                continue
            seen.add((chat_id, None))
            chats.append(await self.livechat_client.get_chat(chat_id))
        return chats


def _attach_followup_messages_to_lingxi_threads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first_assistant_thread_by_chat: dict[str, tuple[datetime, str | None]] = {}
    for row in sorted(rows, key=_row_sort_key):
        if row.get("sender_role") != "assistant":
            continue
        chat_id = str(row.get("chat_id") or "")
        if not chat_id:
            continue
        first_assistant_thread_by_chat.setdefault(chat_id, (_row_sort_at(row), _optional_str(row.get("thread_id"))))

    mapped = []
    for row in rows:
        chat_id = str(row.get("chat_id") or "")
        first_assistant_thread = first_assistant_thread_by_chat.get(chat_id)
        if first_assistant_thread is None:
            mapped.append(row)
            continue
        row_time = _row_sort_at(row)
        first_assistant_time, target_thread_id = first_assistant_thread
        if row_time < first_assistant_time:
            mapped.append(row)
            continue
        original_thread_id = _optional_str(row.get("thread_id"))
        if original_thread_id == target_thread_id:
            mapped.append(row)
            continue
        mapped_row = {**row, "thread_id": target_thread_id, "original_thread_id": original_thread_id}
        conversation_id = mapped_row.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.startswith("livechat:"):
            mapped_row["conversation_id"] = f"livechat:{chat_id}:{target_thread_id or ''}"
        mapped.append(mapped_row)
    return mapped


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


def _placeholders(values: list[Any]) -> str:
    if not values:
        return "NULL"
    return ", ".join(["%s"] * len(values))


def _speaker_name_from_outbound_payload(value: Any) -> str | None:
    payload = _json_loads(value) or {}
    for key in ("sender_name", "author_name", "agent_name", "bot_name"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return None


def _message_from_outbound_staff_reply(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_loads(row.get("payload_json")) or {}
    return {
        "id": f"outbound:{row.get('id')}",
        "outbound_message_id": row.get("id"),
        "conversation_id": f"livechat:{row.get('chat_id')}:{row.get('thread_id') or ''}",
        "chat_id": row.get("chat_id"),
        "thread_id": row.get("thread_id"),
        "sender_role": "agent",
        "message_type": str(row.get("message_type") or payload.get("type") or "text"),
        "text_content": _text_from_outbound_payload(payload),
        "attachment_refs": _attachment_refs_from_outbound_payload(payload),
        "source": "outbound_staff_reply",
        "occurred_at": row.get("sent_at") or row.get("created_at"),
        "created_at": row.get("created_at"),
        "author_id": None,
        "speaker_name": _speaker_name_from_outbound_payload(payload) or "真人客服",
    }


def _message_rows_from_livechat_chat(chat: dict[str, Any], *, self_author_ids: set[str]) -> list[dict[str, Any]]:
    users_by_id = {
        str(user.get("id")): user
        for user in chat.get("users") or []
        if user.get("id") is not None
    }
    rows = []
    for thread in _livechat_threads(chat):
        thread_id = _optional_str(thread.get("id") or thread.get("thread_id"))
        for event in thread.get("events") or []:
            if event.get("type") not in {"message", "file"}:
                continue
            occurred_at = _datetime_from_livechat_value(event.get("created_at"))
            rows.append(
                {
                    "id": f"livechat_api:{event.get('id') or chat.get('id')}",
                    "conversation_id": f"livechat:{chat.get('id')}:{thread_id or ''}",
                    "chat_id": chat.get("id"),
                    "thread_id": thread_id,
                    "sender_role": _sender_role_from_livechat_event(event, users_by_id, self_author_ids),
                    "message_type": str(event.get("type") or "text"),
                    "text_content": _text_from_livechat_event(event),
                    "attachment_refs": _attachment_refs_from_livechat_event(event),
                    "source": "livechat_api",
                    "occurred_at": occurred_at,
                    "created_at": occurred_at,
                    "author_id": event.get("author_id"),
                    "speaker_name": _speaker_name_from_livechat_event(event, users_by_id, self_author_ids),
                }
            )
    return rows


def _metadata_row_from_livechat_chat(chat: dict[str, Any]) -> dict[str, Any]:
    group_ids = sorted(chat_group_ids(chat))
    group_id = group_ids[0] if group_ids else None
    return {
        "chat_id": chat.get("id"),
        "thread_id": None,
        "payload_json": {
            "livechat_group_id": group_id,
            "group_ids": group_ids,
            "platform": platform_for_livechat_group_id(group_id) if group_id is not None else None,
            "chat_users": chat.get("users") or [],
        },
    }


def _livechat_threads(chat: dict[str, Any]) -> list[dict[str, Any]]:
    threads = []
    if isinstance(chat.get("thread"), dict):
        threads.append(chat["thread"])
    if isinstance(chat.get("active_thread"), dict):
        threads.append(chat["active_thread"])
    threads.extend(thread for thread in (chat.get("threads") or []) if isinstance(thread, dict))
    seen = set()
    result = []
    for thread in threads:
        thread_id = str(thread.get("id") or thread.get("thread_id") or id(thread))
        if thread_id in seen:
            continue
        seen.add(thread_id)
        result.append(thread)
    return result


def _sender_role_from_livechat_event(event: dict[str, Any], users_by_id: dict[str, dict[str, Any]], self_author_ids: set[str]) -> str:
    author_id = str(event.get("author_id") or "")
    if author_id in self_author_ids:
        return "assistant"
    author = users_by_id.get(author_id) or {}
    if author.get("type") == "agent":
        return "agent"
    return "customer"


def _speaker_name_from_livechat_event(event: dict[str, Any], users_by_id: dict[str, dict[str, Any]], self_author_ids: set[str]) -> str | None:
    author_id = str(event.get("author_id") or "")
    if author_id in self_author_ids:
        return "LingXi"
    author = users_by_id.get(author_id) or {}
    if author.get("type") != "agent":
        return None
    for key in ("name", "email", "id"):
        value = str(author.get(key) or "").strip()
        if value:
            return value
    return author_id or None


def _text_from_livechat_event(event: dict[str, Any]) -> str | None:
    for key in ("text", "message", "content"):
        value = event.get(key)
        if value is not None:
            return str(value)
    return None


def _attachment_refs_from_livechat_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    if event.get("type") != "file":
        return []
    url = event.get("url") or event.get("file_url")
    filename = event.get("name") or event.get("filename")
    if url or filename:
        return [{"filename": filename, "url": url, "mime_type": event.get("content_type")}]
    return []


def _datetime_from_livechat_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    parsed = parse_rfc3339_to_mysql(str(value)) if value else None
    if parsed:
        return datetime.fromisoformat(parsed)
    return None


def _text_from_outbound_payload(payload: dict[str, Any]) -> str | None:
    for key in ("text", "message", "content"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _attachment_refs_from_outbound_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("url") or payload.get("file_url")
    filename = payload.get("name") or payload.get("filename") or payload.get("asset_key")
    if url or filename:
        return [{"filename": filename, "url": url}]
    return []


def _message_type_from_inbound_payload(payload: dict[str, Any], event_type: Any) -> str:
    event = _payload_event(payload)
    if str(event.get("type") or event_type or "").lower() == "file":
        return "file"
    return str(event.get("type") or event_type or "text")


def _text_from_inbound_payload(payload: dict[str, Any]) -> str | None:
    event = _payload_event(payload)
    for key in ("text", "message", "content"):
        value = event.get(key)
        if value is not None:
            return str(value)
    if event.get("elements") is not None:
        return json.dumps(event.get("elements"), ensure_ascii=False)
    return None


def _attachment_refs_from_inbound_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    event = _payload_event(payload)
    refs = []
    url = event.get("url") or event.get("file_url")
    filename = event.get("name") or event.get("filename")
    if url or filename:
        refs.append({"filename": filename, "url": url})
    return refs


def _speaker_name_from_inbound_payload(payload: dict[str, Any], author_id: str | None) -> str | None:
    if not author_id:
        return None
    for user in payload.get("users") or payload.get("chat_users") or []:
        if str(user.get("id") or "") != str(author_id):
            continue
        for key in ("name", "email", "id"):
            value = str(user.get(key) or "").strip()
            if value:
                return value
    return str(author_id).strip() or None


def _sender_role_from_inbound_event(row: dict[str, Any], *, is_self: bool) -> str:
    if is_self:
        return "assistant"
    if row.get("ignore_reason") == "agent_message":
        return "agent"
    return "customer"


def _source_from_inbound_event(row: dict[str, Any], *, is_self: bool) -> str:
    if is_self:
        return "inbound_event_self"
    if row.get("ignore_reason") == "agent_message":
        return "inbound_event_agent"
    return "inbound_event"


def _speaker_name_for_inbound_event(row: dict[str, Any], payload: dict[str, Any], author_id: str, *, is_self: bool) -> str | None:
    if is_self:
        return "LingXi"
    if row.get("ignore_reason") == "agent_message":
        return _speaker_name_from_inbound_payload(payload, author_id)
    return None


def _payload_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if isinstance(event, dict):
        return event
    return payload


def _row_sort_key(row: dict[str, Any]) -> tuple[datetime, str]:
    return (_row_sort_at(row), str(row.get("id") or ""))


def _row_sort_at(row: dict[str, Any]) -> datetime:
    value = row.get("occurred_at") or row.get("created_at")
    return value if isinstance(value, datetime) else datetime.min


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


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
