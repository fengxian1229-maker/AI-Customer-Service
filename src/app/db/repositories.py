import json
import hashlib

import aiomysql

from app.schemas.events import InboundEvent


class InboundEventRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert(self, event: InboundEvent) -> dict[str, bool]:
        sql = """
        INSERT INTO inbound_events (
          source, raw_action, organization_id, chat_id, thread_id, event_id,
          event_type, standard_event_type, author_id, sender_role, occurred_at,
          dedup_key, payload_json, ignored, ignore_reason
        ) VALUES (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, CAST(%s AS JSON), %s, %s
        )
        ON DUPLICATE KEY UPDATE id = id
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
                inserted = cur.rowcount == 1
                return {"inserted": inserted, "duplicate": not inserted}

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
        async with self.pool.acquire() as conn:
            await self.mark_processed_on_connection(conn, inbound_event_id)

    async def mark_processed_on_connection(self, conn, inbound_event_id: int) -> None:
        sql = "UPDATE inbound_events SET processed = 1 WHERE id = %s"
        async with conn.cursor() as cur:
            await cur.execute(sql, (inbound_event_id,))


class ConversationRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get_or_create(self, chat_id: str, thread_id: str | None = None) -> dict:
        async with self.pool.acquire() as conn:
            return await self.get_or_create_on_connection(conn, chat_id, thread_id)

    async def get_or_create_on_connection(self, conn, chat_id: str, thread_id: str | None = None) -> dict:
        conversation_id = f"livechat:{chat_id}"
        insert_sql = """
        INSERT INTO conversation_states (
          conversation_id, chat_id, current_thread_id
        ) VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE current_thread_id = COALESCE(VALUES(current_thread_id), current_thread_id)
        """
        select_sql = """
        SELECT conversation_id, tenant_id, channel_type, chat_id, current_thread_id,
               status, active_workflow, workflow_stage, slot_memory
        FROM conversation_states
        WHERE chat_id = %s
        """
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(insert_sql, (conversation_id, chat_id, thread_id))
            await cur.execute(select_sql, (chat_id,))
            row = await cur.fetchone()
        if row and row.get("slot_memory") is not None:
            row["slot_memory"] = json_loads(row["slot_memory"])
        return row or {
            "conversation_id": conversation_id,
            "tenant_id": "default",
            "channel_type": "livechat",
            "chat_id": chat_id,
            "current_thread_id": thread_id,
            "status": "AI_ACTIVE",
            "active_workflow": None,
            "workflow_stage": None,
            "slot_memory": {},
        }

    async def update_workflow_state_on_connection(self, conn, conversation_id: str, graph_state: dict) -> None:
        sql = """
        UPDATE conversation_states
        SET status = %s,
            active_workflow = %s,
            workflow_stage = %s,
            slot_memory = CAST(%s AS JSON)
        WHERE conversation_id = %s
        """
        args = (
            graph_state.get("status") or "AI_ACTIVE",
            graph_state.get("active_workflow"),
            graph_state.get("workflow_stage"),
            json_dumps(graph_state.get("slot_memory") or {}),
            conversation_id,
        )
        async with conn.cursor() as cur:
            await cur.execute(sql, args)

    async def update_workflow_state(self, conversation_id: str, graph_state: dict) -> None:
        async with self.pool.acquire() as conn:
            await self.update_workflow_state_on_connection(conn, conversation_id, graph_state)


class OutboundMessageRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert(self, message: dict) -> int:
        async with self.pool.acquire() as conn:
            return await self.insert_on_connection(conn, message)

    async def insert_on_connection(self, conn, message: dict) -> int:
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
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return cur.lastrowid

    async def insert_idempotent(self, message: dict) -> dict:
        async with self.pool.acquire() as conn:
            return await self.insert_idempotent_on_connection(conn, message)

    async def insert_idempotent_on_connection(self, conn, message: dict) -> dict:
        sql = """
        INSERT INTO outbound_messages (
          chat_id, thread_id, action_type, message_type, payload_json,
          status, inbound_event_id, conversation_id
        ) VALUES (%s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s)
        ON DUPLICATE KEY UPDATE id = id
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
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            inserted = cur.rowcount == 1
            return {
                "inserted": inserted,
                "duplicate": not inserted,
                "id": cur.lastrowid if inserted else None,
            }

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

    async def mark_failed(
        self,
        outbound_message_id: int,
        status: str,
        error: str,
        retryable: bool = False,
    ) -> None:
        retry_sql = ", retry_count = retry_count + 1" if retryable else ""
        sql = f"UPDATE outbound_messages SET status = %s, last_error = %s{retry_sql} WHERE id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, error, outbound_message_id))


class ExternalCommandRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert_idempotent(self, command: dict) -> dict:
        async with self.pool.acquire() as conn:
            return await self.insert_idempotent_on_connection(conn, command)

    async def insert_idempotent_on_connection(self, conn, command: dict) -> dict:
        payload = command.get("payload_json") or {}
        dedup_key = command.get("dedup_key") or build_external_command_dedup_key(
            tenant_id=command.get("tenant_id") or "default",
            conversation_id=command["conversation_id"],
            inbound_event_id=command.get("inbound_event_id"),
            command_type=command["command_type"],
            payload=payload,
        )
        sql = """
        INSERT INTO external_commands (
          tenant_id, conversation_id, chat_id, thread_id, inbound_event_id,
          command_type, payload_json, status, retry_count, last_error,
          dedup_key
        ) VALUES (
          %s, %s, %s, %s, %s,
          %s, CAST(%s AS JSON), %s, %s, %s,
          %s
        )
        ON DUPLICATE KEY UPDATE id = id
        """
        args = (
            command.get("tenant_id") or "default",
            command["conversation_id"],
            command["chat_id"],
            command.get("thread_id"),
            command.get("inbound_event_id"),
            command["command_type"],
            json_dumps(payload),
            command.get("status") or "PENDING",
            command.get("retry_count") or 0,
            command.get("last_error"),
            dedup_key,
        )
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            inserted = cur.rowcount == 1
            return {
                "inserted": inserted,
                "duplicate": not inserted,
                "id": cur.lastrowid if inserted else None,
            }

    async def fetch_pending(self, limit: int = 20) -> list[dict]:
        sql = """
        SELECT id, tenant_id, conversation_id, chat_id, thread_id, inbound_event_id,
               command_type, payload_json, status, retry_count, last_error, dedup_key
        FROM external_commands
        WHERE status = 'PENDING'
        ORDER BY created_at ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = json_loads(row["payload_json"])
        return rows

    async def mark_dry_run_done(self, command_id: int) -> None:
        await self._mark_status(command_id, "DRY_RUN_DONE")

    async def mark_sent(self, command_id: int) -> None:
        await self._mark_status(command_id, "SENT")

    async def mark_failed(self, command_id: int, error: str) -> None:
        sql = "UPDATE external_commands SET status = 'FAILED', last_error = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, command_id))

    async def mark_retryable(self, command_id: int, error: str) -> None:
        sql = """
        UPDATE external_commands
        SET status = 'RETRYABLE', retry_count = retry_count + 1, last_error = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, command_id))

    async def _mark_status(self, command_id: int, status: str) -> None:
        sql = f"UPDATE external_commands SET status = '{status}', updated_at = CURRENT_TIMESTAMP WHERE id = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (command_id,))


def json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def json_loads(payload) -> dict:
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def build_external_command_dedup_key(
    tenant_id: str,
    conversation_id: str,
    inbound_event_id: int | None,
    command_type: str,
    payload: dict,
) -> str:
    raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    return f"{tenant_id}:{conversation_id}:{inbound_event_id}:{command_type}:{payload_hash}"


class GatewayTransactionRepository:
    def __init__(
        self,
        pool,
        inbound_repository: InboundEventRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
        outbound_repository: OutboundMessageRepository | None = None,
        external_command_repository: ExternalCommandRepository | None = None,
    ) -> None:
        self.pool = pool
        self.inbound_repository = inbound_repository or InboundEventRepository(pool)
        self.conversation_repository = conversation_repository or ConversationRepository(pool)
        self.outbound_repository = outbound_repository or OutboundMessageRepository(pool)
        self.external_command_repository = external_command_repository or ExternalCommandRepository(pool)

    async def process_event_transactionally(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        outbound_message: dict | list[dict] | None,
        external_commands: list[dict] | None = None,
        graph_state: dict | None = None,
    ) -> dict:
        outbound_messages = []
        if isinstance(outbound_message, list):
            outbound_messages = outbound_message
        elif outbound_message:
            outbound_messages = [outbound_message]
        external_commands = external_commands or []
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                conversation = await self.conversation_repository.get_or_create_on_connection(
                    conn,
                    chat_id=event.chat_id or "unknown",
                    thread_id=event.thread_id,
                )
                if graph_state is not None:
                    await self.conversation_repository.update_workflow_state_on_connection(
                        conn,
                        conversation["conversation_id"],
                        graph_state,
                    )
                outbound_inserts = []
                for message in outbound_messages:
                    message["conversation_id"] = conversation["conversation_id"]
                    outbound_inserts.append(await self.outbound_repository.insert_idempotent_on_connection(conn, message))
                external_command_inserts = []
                for command in external_commands:
                    command["conversation_id"] = conversation["conversation_id"]
                    external_command_inserts.append(await self.external_command_repository.insert_idempotent_on_connection(conn, command))
                await self.inbound_repository.mark_processed_on_connection(conn, inbound_event_id)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return {
            "conversation": conversation,
            "outbound_insert": outbound_inserts[0] if len(outbound_inserts) == 1 else None,
            "outbound_inserts": outbound_inserts,
            "external_command_inserts": external_command_inserts,
        }
