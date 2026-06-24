import json

import aiomysql

from app.schemas.events import InboundEvent


class InboundEventRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert(self, event: InboundEvent) -> bool:
        sql = """
        INSERT IGNORE INTO inbound_events (
          source, raw_action, organization_id, chat_id, thread_id, event_id,
          event_type, standard_event_type, author_id, sender_role, occurred_at,
          dedup_key, payload_json, ignored, ignore_reason
        ) VALUES (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, CAST(%s AS JSON), %s, %s
        )
        """
        args = (
            event.source,
            event.raw_action,
            event.organization_id,
            event.chat_id,
            event.thread_id,
            event.event_id,
            event.event_type,
            event.standard_event_type,
            event.author_id,
            event.sender_role,
            event.occurred_at,
            event.dedup_key,
            json_dumps(event.payload_json),
            1 if event.ignored else 0,
            event.ignore_reason,
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.rowcount == 1

    async def fetch_unprocessed(self, limit: int = 20) -> list[dict]:
        sql = """
        SELECT id, chat_id, thread_id, event_id, event_type, standard_event_type,
               author_id, sender_role, occurred_at, dedup_key, payload_json,
               raw_action, source, organization_id, ignored, ignore_reason
        FROM inbound_events
        WHERE processed = 0 AND ignored = 0
        ORDER BY id ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = json_loads(row["payload_json"])
        return rows

    async def mark_processed(self, inbound_event_id: int) -> None:
        sql = "UPDATE inbound_events SET processed = 1 WHERE id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (inbound_event_id,))


class ConversationRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
        conversation_id = f"livechat:{chat_id}"
        insert_sql = """
        INSERT INTO conversation_states (
          conversation_id, chat_id, current_thread_id
        ) VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE current_thread_id = COALESCE(VALUES(current_thread_id), current_thread_id)
        """
        select_sql = "SELECT conversation_id, chat_id, current_thread_id FROM conversation_states WHERE chat_id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(insert_sql, (conversation_id, chat_id, thread_id))
                await cur.execute(select_sql, (chat_id,))
                row = await cur.fetchone()
        return row or {"conversation_id": conversation_id, "chat_id": chat_id, "current_thread_id": thread_id}


class OutboundMessageRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert(self, message: dict) -> int:
        sql = """
        INSERT INTO outbound_messages (
          chat_id, thread_id, action_type, message_type, payload_json,
          status, inbound_event_id, conversation_id
        ) VALUES (%s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s)
        """
        args = (
            message["chat_id"],
            message["thread_id"],
            message["action_type"],
            message["message_type"],
            json_dumps(message["payload_json"]),
            message["status"],
            message["inbound_event_id"],
            message["conversation_id"],
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.lastrowid

    async def fetch_pending(self, limit: int = 20) -> list[dict]:
        sql = """
        SELECT id, chat_id, thread_id, payload_json, status
        FROM outbound_messages
        WHERE status = 'PENDING'
        ORDER BY id ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = json_loads(row["payload_json"])
        return rows

    async def mark_sent(self, outbound_message_id: int) -> None:
        sql = "UPDATE outbound_messages SET status = 'SENT', sent_at = NOW(6), last_error = NULL WHERE id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (outbound_message_id,))

    async def mark_failed(self, outbound_message_id: int, error: str) -> None:
        sql = "UPDATE outbound_messages SET status = 'FAILED', last_error = %s WHERE id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, outbound_message_id))


def json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def json_loads(payload) -> dict:
    if isinstance(payload, str):
        return json.loads(payload)
    return payload
