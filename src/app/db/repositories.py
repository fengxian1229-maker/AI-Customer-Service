import json
import hashlib
from datetime import datetime

import aiomysql

from app.schemas.events import InboundEvent
from app.services.knowledge_blocks import normalize_metadata_json, normalize_question_aliases, validate_answer_blocks
from app.services.rag import rank_knowledge_document
from app.workflows.slot_extractors import normalize_text


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
            self._normalize_inbound_row(row)
        return rows

    async def fetch_unprocessed_by_id(self, inbound_event_id: int) -> dict | None:
        sql = """
        SELECT id, chat_id, thread_id, event_id, event_type, standard_event_type,
               author_id, sender_role, occurred_at, dedup_key, payload_json,
               raw_action, source, organization_id, ignored, ignore_reason
        FROM inbound_events
        WHERE id = %s
          AND processed = 0
          AND ignored = 0
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (inbound_event_id,))
                row = await cur.fetchone()
        if not row:
            return None
        self._normalize_inbound_row(row)
        return row

    def _normalize_inbound_row(self, row: dict) -> None:
        row["payload_json"] = json_loads(row["payload_json"])
        if isinstance(row.get("occurred_at"), datetime):
            row["occurred_at"] = row["occurred_at"].strftime("%Y-%m-%d %H:%M:%S.%f")

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
        SET status = COALESCE(%s, status),
            active_workflow = COALESCE(%s, active_workflow),
            workflow_stage = COALESCE(%s, workflow_stage),
            slot_memory = CAST(%s AS JSON)
        WHERE conversation_id = %s
        """
        args = (
            graph_state.get("status"),
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
          status, inbound_event_id, conversation_id,
          dedup_key, block_index, message_kind, command_type
        ) VALUES (
          %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s,
          %s, %s, %s, %s
        )
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
            _outbound_dedup_key(message),
            message.get("block_index"),
            message.get("message_kind") or message.get("message_type"),
            message.get("command_type") or message["action_type"],
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
          status, inbound_event_id, conversation_id,
          dedup_key, block_index, message_kind, command_type
        ) VALUES (
          %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s,
          %s, %s, %s, %s
        )
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
            _outbound_dedup_key(message),
            message.get("block_index"),
            message.get("message_kind") or message.get("message_type"),
            message.get("command_type") or message["action_type"],
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
        SELECT m.id, COALESCE(c.tenant_id, 'default') AS tenant_id,
               COALESCE(c.channel_type, 'livechat') AS channel_type,
               m.conversation_id, m.inbound_event_id, m.chat_id, m.thread_id,
               m.action_type, m.message_type, m.payload_json, m.status,
               m.dedup_key, m.block_index, m.message_kind, m.command_type,
               c.status AS conversation_status,
               c.active_workflow AS conversation_active_workflow,
               c.workflow_stage AS conversation_workflow_stage
        FROM outbound_messages m
        LEFT JOIN conversation_states c ON c.conversation_id = m.conversation_id
        WHERE m.status = 'PENDING'
        ORDER BY m.id ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = json_loads(row["payload_json"])
        return rows

    async def fetch_pending_by_inbound_event(self, inbound_event_id: int, limit: int = 20) -> list[dict]:
        sql = """
        SELECT m.id, COALESCE(c.tenant_id, 'default') AS tenant_id,
               COALESCE(c.channel_type, 'livechat') AS channel_type,
               m.conversation_id, m.inbound_event_id, m.chat_id, m.thread_id,
               m.action_type, m.message_type, m.payload_json, m.status,
               m.dedup_key, m.block_index, m.message_kind, m.command_type,
               c.status AS conversation_status,
               c.active_workflow AS conversation_active_workflow,
               c.workflow_stage AS conversation_workflow_stage
        FROM outbound_messages m
        LEFT JOIN conversation_states c ON c.conversation_id = m.conversation_id
        WHERE m.status = 'PENDING'
          AND m.inbound_event_id = %s
        ORDER BY m.id ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (inbound_event_id, limit))
                rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = json_loads(row["payload_json"])
        return rows

    async def mark_sent(self, outbound_message_id: int) -> None:
        async with self.pool.acquire() as conn:
            await self.mark_sent_on_connection(conn, outbound_message_id)

    async def mark_sent_on_connection(self, conn, outbound_message_id: int) -> None:
        sql = "UPDATE outbound_messages SET status = 'SENT', sent_at = NOW(6), last_error = NULL WHERE id = %s"
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

    async def mark_pending_by_inbound_event_skipped(
        self,
        inbound_event_id: int,
        status: str = "SKIPPED_MANUAL_SMOKE",
        error: str = "manual smoke uses fake chat_id; not sent",
    ) -> int:
        sql = """
        UPDATE outbound_messages
        SET status = %s, last_error = %s
        WHERE inbound_event_id = %s
          AND status = 'PENDING'
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, error, inbound_event_id))
                return cur.rowcount


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
               command_type, payload_json, status, retry_count, last_error,
               leased_at, lease_expires_at, locked_by, attempted_at, processed_at,
               dedup_key
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

    async def mark_pending_by_inbound_event_skipped(
        self,
        inbound_event_id: int,
        status: str = "SKIPPED_MANUAL_SMOKE",
        error: str = "manual guarded smoke dry-run; external command not executed",
    ) -> int:
        sql = """
        UPDATE external_commands
        SET status = %s,
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE inbound_event_id = %s
          AND status = 'PENDING'
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, error, inbound_event_id))
                return cur.rowcount

    async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                rows = await self._lease_pending_on_connection(conn, limit, worker_id, lease_seconds)
                await conn.commit()
                return rows
            except Exception:
                await conn.rollback()
                raise

    async def _lease_pending_on_connection(self, conn, limit: int, worker_id: str, lease_seconds: int) -> list[dict]:
        select_sql = """
        SELECT id
        FROM external_commands
        WHERE status IN ('PENDING', 'RETRYABLE')
          AND (locked_by IS NULL OR lease_expires_at IS NULL OR lease_expires_at < NOW(6))
        ORDER BY created_at ASC
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(select_sql, (limit,))
            id_rows = await cur.fetchall()
            ids = [row["id"] for row in id_rows]
            if not ids:
                return []

            placeholders = ", ".join(["%s"] * len(ids))
            update_sql = f"""
            UPDATE external_commands
            SET leased_at = NOW(6),
                lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
                locked_by = %s,
                attempted_at = NOW(6),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """
            await cur.execute(update_sql, (lease_seconds, worker_id, *ids))

            fetch_sql = f"""
            SELECT id, tenant_id, conversation_id, chat_id, thread_id, inbound_event_id,
                   command_type, payload_json, status, retry_count, last_error,
                   leased_at, lease_expires_at, locked_by, attempted_at, processed_at,
                   dedup_key
            FROM external_commands
            WHERE id IN ({placeholders})
            ORDER BY created_at ASC
            """
            await cur.execute(fetch_sql, tuple(ids))
            rows = await cur.fetchall()
        for row in rows:
            row["payload_json"] = json_loads(row["payload_json"])
        return rows

    async def release_lease(self, command_id: int) -> None:
        sql = """
        UPDATE external_commands
        SET leased_at = NULL, lease_expires_at = NULL, locked_by = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (command_id,))

    async def mark_dry_run_done(self, command_id: int) -> None:
        await self._mark_status(command_id, "DRY_RUN_DONE")

    async def mark_sent(self, command_id: int) -> None:
        await self._mark_status(command_id, "SENT")

    async def mark_failed(self, command_id: int, error: str) -> None:
        sql = """
        UPDATE external_commands
        SET status = 'FAILED',
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, command_id))

    async def mark_retryable(self, command_id: int, error: str) -> None:
        sql = """
        UPDATE external_commands
        SET status = 'RETRYABLE',
            retry_count = retry_count + 1,
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, command_id))

    async def mark_status(self, command_id: int, status: str, error: str | None = None) -> None:
        processed_sql = ", processed_at = NOW(6)" if status in {"DRY_RUN_DONE", "SENT", "PROCESSED", "DONE"} else ""
        sql = f"""
        UPDATE external_commands
        SET status = %s,
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL{processed_sql},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, error, command_id))

    async def merge_payload_json(self, command_id: int, patch: dict) -> None:
        sql = """
        UPDATE external_commands
        SET payload_json = JSON_MERGE_PATCH(COALESCE(payload_json, JSON_OBJECT()), CAST(%s AS JSON)),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (json_dumps(patch), command_id))

    async def mark_processing_failed(self, command_id: int, error: str, max_retries: int = 3) -> str:
        sql = """
        UPDATE external_commands
        SET retry_count = retry_count + 1,
            status = CASE WHEN retry_count + 1 >= %s THEN 'FAILED' ELSE 'RETRYABLE' END,
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (max_retries, error, command_id))
        return "RETRYABLE"

    async def mark_processing_failed_and_get_status(self, command_id: int, error: str, max_retries: int = 3) -> str:
        await self.mark_processing_failed(command_id, error, max_retries=max_retries)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT status FROM external_commands WHERE id = %s", (command_id,))
                row = await cur.fetchone()
        return row[0] if row else "FAILED"

    async def lease_pending_by_id(self, command_id: int, worker_id: str, lease_seconds: int) -> dict | None:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                row = await self._lease_pending_by_id_on_connection(conn, command_id, worker_id, lease_seconds)
                await conn.commit()
                return row
            except Exception:
                await conn.rollback()
                raise

    async def _lease_pending_by_id_on_connection(self, conn, command_id: int, worker_id: str, lease_seconds: int) -> dict | None:
        select_sql = """
        SELECT id
        FROM external_commands
        WHERE id = %s
          AND status IN ('PENDING', 'RETRYABLE')
          AND (locked_by IS NULL OR lease_expires_at IS NULL OR lease_expires_at < NOW(6))
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(select_sql, (command_id,))
            row = await cur.fetchone()
            if not row:
                return None
            update_sql = """
            UPDATE external_commands
            SET leased_at = NOW(6),
                lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
                locked_by = %s,
                attempted_at = NOW(6),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """
            await cur.execute(update_sql, (lease_seconds, worker_id, command_id))
            fetch_sql = """
            SELECT id, tenant_id, conversation_id, chat_id, thread_id, inbound_event_id,
                   command_type, payload_json, status, retry_count, last_error,
                   leased_at, lease_expires_at, locked_by, attempted_at, processed_at,
                   dedup_key
            FROM external_commands
            WHERE id = %s
            LIMIT 1
            """
            await cur.execute(fetch_sql, (command_id,))
            leased = await cur.fetchone()
        if leased:
            leased["payload_json"] = json_loads(leased["payload_json"])
        return leased

    async def recover_expired_leases(self) -> int:
        sql = """
        UPDATE external_commands
        SET leased_at = NULL, lease_expires_at = NULL, locked_by = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('PENDING', 'RETRYABLE')
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < NOW(6)
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, ())
                return cur.rowcount

    async def _mark_status(self, command_id: int, status: str) -> None:
        processed_sql = ", processed_at = NOW(6)" if status in {"DRY_RUN_DONE", "SENT"} else ""
        sql = f"""
        UPDATE external_commands
        SET status = '{status}',
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL{processed_sql},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (command_id,))


class ExternalCommandResultRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert_idempotent(self, result: dict) -> dict:
        async with self.pool.acquire() as conn:
            return await self.insert_idempotent_on_connection(conn, result)

    async def insert_idempotent_on_connection(self, conn, result: dict) -> dict:
        result_json = result.get("result_json") or {}
        dedup_key = result.get("dedup_key") or build_external_command_result_dedup_key(
            tenant_id=result.get("tenant_id") or "default",
            conversation_id=result["conversation_id"],
            external_command_id=result["external_command_id"],
            command_type=result["command_type"],
            result_type=result["result_type"],
            result=result_json,
        )
        sql = """
        INSERT INTO external_command_results (
          external_command_id, tenant_id, conversation_id, chat_id, thread_id,
          inbound_event_id, command_type, result_type, result_json, status,
          processed_at, last_error, dedup_key
        ) VALUES (
          %s, %s, %s, %s, %s,
          %s, %s, %s, CAST(%s AS JSON), %s,
          %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE id = id
        """
        args = (
            result["external_command_id"],
            result.get("tenant_id") or "default",
            result["conversation_id"],
            result["chat_id"],
            result.get("thread_id"),
            result.get("inbound_event_id"),
            result["command_type"],
            result["result_type"],
            json_dumps(result_json),
            result.get("status") or "PENDING",
            result.get("processed_at"),
            result.get("last_error"),
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
        SELECT id, external_command_id, tenant_id, conversation_id, chat_id, thread_id,
               inbound_event_id, command_type, result_type, result_json, status, retry_count,
               processed_at, last_error, leased_at, lease_expires_at, locked_by,
               attempted_at, dedup_key
        FROM external_command_results
        WHERE status = 'PENDING'
        ORDER BY created_at ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        for row in rows:
            row["result_json"] = json_loads(row["result_json"])
        return rows

    async def lease_pending(self, limit: int, worker_id: str, lease_seconds: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                rows = await self._lease_pending_on_connection(conn, limit, worker_id, lease_seconds)
                await conn.commit()
                return rows
            except Exception:
                await conn.rollback()
                raise

    async def _lease_pending_on_connection(self, conn, limit: int, worker_id: str, lease_seconds: int) -> list[dict]:
        select_sql = """
        SELECT id
        FROM external_command_results
        WHERE status IN ('PENDING', 'RETRYABLE')
          AND (locked_by IS NULL OR lease_expires_at IS NULL OR lease_expires_at < NOW(6))
        ORDER BY created_at ASC
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(select_sql, (limit,))
            id_rows = await cur.fetchall()
            ids = [row["id"] for row in id_rows]
            if not ids:
                return []

            placeholders = ", ".join(["%s"] * len(ids))
            update_sql = f"""
            UPDATE external_command_results
            SET leased_at = NOW(6),
                lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
                locked_by = %s,
                attempted_at = NOW(6),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """
            await cur.execute(update_sql, (lease_seconds, worker_id, *ids))

            fetch_sql = f"""
            SELECT id, external_command_id, tenant_id, conversation_id, chat_id, thread_id,
                   inbound_event_id, command_type, result_type, result_json, status, retry_count,
                   processed_at, last_error, leased_at, lease_expires_at, locked_by,
                   attempted_at, dedup_key
            FROM external_command_results
            WHERE id IN ({placeholders})
            ORDER BY created_at ASC
            """
            await cur.execute(fetch_sql, tuple(ids))
            rows = await cur.fetchall()
        for row in rows:
            row["result_json"] = json_loads(row["result_json"])
        return rows

    async def release_lease(self, result_id: int) -> None:
        sql = """
        UPDATE external_command_results
        SET leased_at = NULL, lease_expires_at = NULL, locked_by = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (result_id,))

    async def mark_processed(self, result_id: int) -> None:
        async with self.pool.acquire() as conn:
            await self.mark_processed_on_connection(conn, result_id)

    async def mark_processed_on_connection(self, conn, result_id: int) -> None:
        sql = """
        UPDATE external_command_results
        SET status = 'PROCESSED',
            processed_at = NOW(6),
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with conn.cursor() as cur:
            await cur.execute(sql, (result_id,))

    async def mark_failed(self, result_id: int, error: str) -> None:
        sql = """
        UPDATE external_command_results
        SET status = 'FAILED',
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, result_id))

    async def mark_retryable(self, result_id: int, error: str) -> None:
        sql = """
        UPDATE external_command_results
        SET status = 'RETRYABLE',
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error, result_id))

    async def mark_processing_failed(self, result_id: int, error: str, max_retries: int = 3) -> None:
        sql = """
        UPDATE external_command_results
        SET retry_count = retry_count + 1,
            status = CASE WHEN retry_count + 1 >= %s THEN 'FAILED' ELSE 'RETRYABLE' END,
            last_error = %s,
            leased_at = NULL,
            lease_expires_at = NULL,
            locked_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (max_retries, error, result_id))

    async def recover_expired_leases(self) -> int:
        sql = """
        UPDATE external_command_results
        SET leased_at = NULL, lease_expires_at = NULL, locked_by = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('PENDING', 'RETRYABLE')
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < NOW(6)
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, ())
                return cur.rowcount


class ConversationMessageRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert_idempotent(self, message: dict) -> dict:
        async with self.pool.acquire() as conn:
            return await self.insert_idempotent_on_connection(conn, message)

    async def insert_idempotent_on_connection(self, conn, message: dict) -> dict:
        sql = """
        INSERT INTO conversation_messages (
          conversation_id, tenant_id, channel_type, chat_id, thread_id,
          inbound_event_id, outbound_message_id, external_command_result_id,
          sender_role, message_type, text_content, attachment_refs, source, occurred_at
        ) VALUES (
          %s, %s, %s, %s, %s,
          %s, %s, %s,
          %s, %s, %s, CAST(%s AS JSON), %s, %s
        )
        ON DUPLICATE KEY UPDATE id = id
        """
        args = (
            message["conversation_id"],
            message.get("tenant_id") or "default",
            message.get("channel_type") or "livechat",
            message.get("chat_id"),
            message.get("thread_id"),
            message.get("inbound_event_id"),
            message.get("outbound_message_id"),
            message.get("external_command_result_id"),
            message["sender_role"],
            message.get("message_type") or "text",
            message.get("text_content"),
            json.dumps(message.get("attachment_refs") or [], ensure_ascii=False, separators=(",", ":")),
            message["source"],
            message.get("occurred_at"),
        )
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            inserted = cur.rowcount == 1
            return {"inserted": inserted, "duplicate": not inserted, "id": cur.lastrowid if inserted else None}

    async def fetch_recent(self, conversation_id: str, limit: int = 10) -> list[dict]:
        sql = """
        SELECT id, conversation_id, sender_role, message_type, text_content,
               attachment_refs, source, created_at
        FROM conversation_messages
        WHERE conversation_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id, limit))
                rows = await cur.fetchall()
        rows = list(reversed(rows))
        for row in rows:
            row["attachment_refs"] = json_loads(row.get("attachment_refs") or "[]")
        return rows


class KnowledgeDocumentRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert_idempotent(self, document: dict) -> dict:
        sql = """
        INSERT INTO knowledge_documents (
          tenant_id, kb_scope, title, content, keywords,
          question_aliases, answer_blocks, metadata_json,
          language, priority, enabled
        ) VALUES (
          %s, %s, %s, %s, CAST(%s AS JSON),
          CAST(%s AS JSON), CAST(%s AS JSON), CAST(%s AS JSON),
          %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
          content = VALUES(content),
          keywords = VALUES(keywords),
          question_aliases = VALUES(question_aliases),
          answer_blocks = VALUES(answer_blocks),
          metadata_json = VALUES(metadata_json),
          language = VALUES(language),
          priority = VALUES(priority),
          enabled = VALUES(enabled),
          updated_at = CURRENT_TIMESTAMP
        """
        question_aliases_json = (
            json.dumps(normalize_question_aliases(document.get("question_aliases")), ensure_ascii=False, separators=(",", ":"))
            if "question_aliases" in document
            else None
        )
        answer_blocks_json = (
            json.dumps(validate_answer_blocks(document.get("answer_blocks")), ensure_ascii=False, separators=(",", ":"))
            if "answer_blocks" in document and document.get("answer_blocks") is not None
            else None
        )
        metadata_json = (
            json.dumps(normalize_metadata_json(document.get("metadata_json")), ensure_ascii=False, separators=(",", ":"))
            if "metadata_json" in document
            else None
        )
        args = (
            document.get("tenant_id") or "default",
            document.get("kb_scope") or "default",
            document["title"],
            document["content"],
            json.dumps(document.get("keywords") or [], ensure_ascii=False, separators=(",", ":")),
            question_aliases_json,
            answer_blocks_json,
            metadata_json,
            document.get("language"),
            document.get("priority", 100),
            1 if document.get("enabled", True) else 0,
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                inserted = cur.rowcount == 1
                return {
                    "inserted": inserted,
                    "duplicate": not inserted,
                    "id": cur.lastrowid,
                }

    async def list_documents(
        self,
        tenant_id: str,
        kb_scope: str = "default",
        enabled: bool | None = None,
        limit: int = 50,
    ) -> list[dict]:
        sql = """
        SELECT id, tenant_id, kb_scope, title, content, keywords,
               question_aliases, answer_blocks, metadata_json,
               language, priority, enabled, created_at, updated_at
        FROM knowledge_documents
        WHERE tenant_id = %s
          AND kb_scope = %s
        """
        args: list[object] = [tenant_id, kb_scope]
        if enabled is not None:
            sql += "\n  AND enabled = %s"
            args.append(1 if enabled else 0)
        sql += "\n        ORDER BY priority ASC, id ASC\n        LIMIT %s"
        args.append(limit)
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, tuple(args))
                rows = await cur.fetchall()
        return [self._decode_document(row) for row in rows]

    async def get_by_title(self, tenant_id: str, kb_scope: str, title: str) -> dict | None:
        sql = """
        SELECT id, tenant_id, kb_scope, title, content, keywords,
               question_aliases, answer_blocks, metadata_json,
               language, priority, enabled, created_at, updated_at
        FROM knowledge_documents
        WHERE tenant_id = %s AND kb_scope = %s AND title = %s
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (tenant_id, kb_scope, title))
                row = await cur.fetchone()
        return self._decode_document(row) if row else None

    async def set_enabled(self, tenant_id: str, kb_scope: str, title: str, enabled: bool) -> dict:
        sql = """
        UPDATE knowledge_documents
        SET enabled = %s, updated_at = CURRENT_TIMESTAMP
        WHERE tenant_id = %s AND kb_scope = %s AND title = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (1 if enabled else 0, tenant_id, kb_scope, title))
                return {"updated": cur.rowcount > 0, "rowcount": cur.rowcount}

    async def search(
        self,
        tenant_id: str,
        query: str,
        kb_scope: str = "default",
        limit: int = 3,
    ) -> list[dict]:
        sql = """
        SELECT id, tenant_id, kb_scope, title, content, keywords,
               question_aliases, answer_blocks, metadata_json,
               language, priority
        FROM knowledge_documents
        WHERE tenant_id = %s
          AND kb_scope = %s
          AND enabled = 1
        ORDER BY priority ASC, id ASC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (tenant_id, kb_scope))
                rows = await cur.fetchall()

        query_text = normalize_text(query)
        scored = []
        for row in rows:
            decoded = self._decode_document(row)
            ranked = rank_knowledge_document(decoded, query_text)
            if ranked["score"] > 0:
                scored.append({**decoded, **ranked})
        scored.sort(key=lambda item: (-item["score"], item.get("priority", 100), item["id"]))
        return scored[:limit]

    def _decode_document(self, row: dict | None) -> dict | None:
        if row is None:
            return None
        decoded = dict(row)
        decoded["keywords"] = json_loads(decoded.get("keywords") or "[]")
        decoded["question_aliases"] = json_loads(decoded.get("question_aliases") or "[]")
        decoded["answer_blocks"] = json_loads(decoded.get("answer_blocks")) if decoded.get("answer_blocks") is not None else None
        decoded["metadata_json"] = json_loads(decoded.get("metadata_json") or "{}")
        return decoded


class GraphRunErrorRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert(self, error_record: dict) -> int:
        sql = """
        INSERT INTO graph_run_errors (
          conversation_id, inbound_event_id, graph_thread_id, node_name,
          error_type, error_message, retryable, state_snapshot
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON))
        """
        state_snapshot = error_record.get("state_snapshot")
        state_snapshot_json = (
            json.dumps(state_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if state_snapshot is not None
            else None
        )
        args = (
            error_record["conversation_id"],
            error_record["inbound_event_id"],
            error_record.get("graph_thread_id"),
            error_record.get("node_name"),
            error_record["error_type"],
            error_record["error_message"],
            error_record.get("retryable", 0),
            state_snapshot_json,
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.lastrowid

    async def fetch_recent(self, conversation_id: str, limit: int = 20) -> list[dict]:
        sql = """
        SELECT id, conversation_id, inbound_event_id, graph_thread_id, node_name,
               error_type, error_message, retryable, state_snapshot, created_at
        FROM graph_run_errors
        WHERE conversation_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id, limit))
                rows = await cur.fetchall()
        for row in rows:
            row["state_snapshot"] = json_loads(row["state_snapshot"])
        return rows

    async def fetch_retryable(self, limit: int = 20) -> list[dict]:
        sql = """
        SELECT id, conversation_id, inbound_event_id, graph_thread_id, node_name,
               error_type, error_message, retryable, state_snapshot, created_at
        FROM graph_run_errors
        WHERE retryable = 1
        ORDER BY created_at ASC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        for row in rows:
            row["state_snapshot"] = json_loads(row["state_snapshot"])
        return rows

    async def list_errors(
        self,
        conversation_id: str | None = None,
        graph_thread_id: str | None = None,
        inbound_event_id: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict]:
        del status
        where_clauses = []
        args: list[object] = []
        if conversation_id:
            where_clauses.append("conversation_id = %s")
            args.append(conversation_id)
        if graph_thread_id:
            where_clauses.append("graph_thread_id = %s")
            args.append(graph_thread_id)
        if inbound_event_id is not None:
            where_clauses.append("inbound_event_id = %s")
            args.append(inbound_event_id)
        if created_after:
            where_clauses.append("created_at >= %s")
            args.append(created_after)
        if created_before:
            where_clauses.append("created_at <= %s")
            args.append(created_before)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"""
        SELECT id, conversation_id, inbound_event_id, graph_thread_id, node_name,
               error_type, error_message, retryable, state_snapshot, created_at
        FROM graph_run_errors
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %s
        """
        args.append(limit)
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, tuple(args))
                rows = await cur.fetchall()
        for row in rows:
            row["state_snapshot"] = json_loads(row["state_snapshot"])
        return rows


class GraphCheckpointRunRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert_run(self, record: dict) -> int:
        sql = """
        INSERT INTO graph_checkpoint_runs (
          conversation_id, graph_thread_id, checkpoint_mode, status,
          inbound_event_id, latest_checkpoint_id, metadata_json
        ) VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS JSON))
        """
        args = (
            record["conversation_id"],
            record["graph_thread_id"],
            record["checkpoint_mode"],
            record.get("status") or "CREATED",
            record.get("inbound_event_id"),
            record.get("latest_checkpoint_id"),
            json_dumps(_sanitize_checkpoint_metadata(record.get("metadata_json") or {})),
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.lastrowid

    async def mark_succeeded(
        self,
        run_id: int,
        latest_checkpoint_id: str | None = None,
        metadata_json: dict | None = None,
    ) -> None:
        metadata_sql = ""
        args: tuple
        if metadata_json:
            metadata_sql = """,
            metadata_json = JSON_MERGE_PATCH(COALESCE(metadata_json, JSON_OBJECT()), CAST(%s AS JSON))"""
            args = (latest_checkpoint_id, json_dumps(_sanitize_checkpoint_metadata(metadata_json)), run_id)
        else:
            args = (latest_checkpoint_id, run_id)
        sql = f"""
        UPDATE graph_checkpoint_runs
        SET status = 'SUCCEEDED',
            latest_checkpoint_id = %s,
            error_type = NULL,
            error_message = NULL{metadata_sql},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)

    async def mark_failed(self, run_id: int, error: Exception | str) -> None:
        error_type = type(error).__name__ if isinstance(error, Exception) else "RuntimeError"
        error_message = str(error)
        sql = """
        UPDATE graph_checkpoint_runs
        SET status = 'FAILED',
            error_type = %s,
            error_message = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (error_type, error_message, run_id))

    async def fetch_recent(self, conversation_id: str, limit: int = 20) -> list[dict]:
        sql = """
        SELECT id, conversation_id, graph_thread_id, checkpoint_mode, status,
               inbound_event_id, latest_checkpoint_id, error_type, error_message,
               metadata_json, created_at, updated_at
        FROM graph_checkpoint_runs
        WHERE conversation_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id, limit))
                rows = await cur.fetchall()
        for row in rows:
            row["metadata_json"] = json_loads(row.get("metadata_json") or "{}")
        return rows

    async def list_runs(
        self,
        conversation_id: str | None = None,
        graph_thread_id: str | None = None,
        inbound_event_id: int | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        where_clauses = []
        args: list[object] = []
        if conversation_id:
            where_clauses.append("conversation_id = %s")
            args.append(conversation_id)
        if graph_thread_id:
            where_clauses.append("graph_thread_id = %s")
            args.append(graph_thread_id)
        if inbound_event_id is not None:
            where_clauses.append("inbound_event_id = %s")
            args.append(inbound_event_id)
        if status:
            where_clauses.append("status = %s")
            args.append(status)
        if created_after:
            where_clauses.append("created_at >= %s")
            args.append(created_after)
        if created_before:
            where_clauses.append("created_at <= %s")
            args.append(created_before)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"""
        SELECT id, conversation_id, graph_thread_id, checkpoint_mode, status,
               inbound_event_id, latest_checkpoint_id, error_type, error_message,
               metadata_json, created_at, updated_at
        FROM graph_checkpoint_runs
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %s
        """
        args.append(limit)
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, tuple(args))
                rows = await cur.fetchall()
        for row in rows:
            row["metadata_json"] = json_loads(row.get("metadata_json") or "{}")
        return rows

    async def get_run(self, run_id: int) -> dict | None:
        sql = """
        SELECT id, conversation_id, graph_thread_id, checkpoint_mode, status,
               inbound_event_id, latest_checkpoint_id, error_type, error_message,
               metadata_json, created_at, updated_at
        FROM graph_checkpoint_runs
        WHERE id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (run_id,))
                row = await cur.fetchone()
        if row:
            row["metadata_json"] = json_loads(row.get("metadata_json") or "{}")
        return row

    async def fetch_latest(
        self,
        conversation_id: str | None = None,
        graph_thread_id: str | None = None,
        inbound_event_id: int | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> dict | None:
        rows = await self.list_runs(
            conversation_id=conversation_id,
            graph_thread_id=graph_thread_id,
            inbound_event_id=inbound_event_id,
            status=status,
            created_after=created_after,
            created_before=created_before,
            limit=1,
        )
        return rows[0] if rows else None


class FaqSmokeReadRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def latest_inbound(
        self,
        conversation_id: str | None = None,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        effective_chat_id = chat_id or _chat_id_from_conversation_id(conversation_id)
        sql = """
        SELECT id, source, raw_action, chat_id, thread_id, event_id,
               standard_event_type, author_id, sender_role, processed,
               ignored, ignore_reason, occurred_at, created_at, payload_json
        FROM inbound_events
        """
        where, args = self._build_where(
            {
                "chat_id": effective_chat_id,
                "id": inbound_event_id,
            }
        )
        rows = await self._fetch_rows(f"{sql}{where} ORDER BY id DESC LIMIT %s", (*args, limit))
        for row in rows:
            payload = json_loads(row.pop("payload_json"))
            row["text"] = _extract_payload_text(payload)
        return rows

    async def latest_outbound(
        self,
        conversation_id: str | None = None,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        sql = """
        SELECT id, conversation_id, inbound_event_id, chat_id, thread_id,
               action_type, command_type, message_type, message_kind, block_index,
               status, retry_count, last_error, sent_at, created_at, payload_json
        FROM outbound_messages
        """
        where, args = self._build_where(
            {
                "conversation_id": conversation_id,
                "chat_id": chat_id,
                "inbound_event_id": inbound_event_id,
            }
        )
        rows = await self._fetch_rows(f"{sql}{where} ORDER BY id DESC LIMIT %s", (*args, limit))
        for row in rows:
            payload = json_loads(row.pop("payload_json"))
            row["text"] = _extract_payload_text(payload)
        return rows

    async def latest_conversation(
        self,
        conversation_id: str | None = None,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        sql = """
        SELECT id, conversation_id, inbound_event_id, outbound_message_id,
               sender_role, message_type, text_content, source, created_at
        FROM conversation_messages
        """
        where, args = self._build_where(
            {
                "conversation_id": conversation_id,
                "chat_id": chat_id,
                "inbound_event_id": inbound_event_id,
            }
        )
        return await self._fetch_rows(f"{sql}{where} ORDER BY id DESC LIMIT %s", (*args, limit))

    async def latest_checkpoints(
        self,
        conversation_id: str | None = None,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        effective_conversation_id = conversation_id or _conversation_id_from_chat_id(chat_id)
        sql = """
        SELECT id, conversation_id, graph_thread_id, checkpoint_mode, status,
               inbound_event_id, error_type, error_message, created_at, updated_at
        FROM graph_checkpoint_runs
        """
        where, args = self._build_where(
            {
                "conversation_id": effective_conversation_id,
                "inbound_event_id": inbound_event_id,
            }
        )
        return await self._fetch_rows(f"{sql}{where} ORDER BY id DESC LIMIT %s", (*args, limit))

    async def latest_errors(
        self,
        conversation_id: str | None = None,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        effective_conversation_id = conversation_id or _conversation_id_from_chat_id(chat_id)
        sql = """
        SELECT id, conversation_id, inbound_event_id, graph_thread_id,
               node_name, error_type, error_message, retryable, created_at
        FROM graph_run_errors
        """
        where, args = self._build_where(
            {
                "conversation_id": effective_conversation_id,
                "inbound_event_id": inbound_event_id,
            }
        )
        return await self._fetch_rows(f"{sql}{where} ORDER BY id DESC LIMIT %s", (*args, limit))

    async def summary(
        self,
        conversation_id: str | None = None,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> dict:
        conversation_id, chat_id = _normalize_smoke_scope(conversation_id, chat_id)
        inbound = await self.latest_inbound(conversation_id, chat_id, inbound_event_id, limit)
        outbound = await self.latest_outbound(conversation_id, chat_id, inbound_event_id, limit)
        conversation = await self.latest_conversation(conversation_id, chat_id, inbound_event_id, limit)
        checkpoints = await self.latest_checkpoints(conversation_id, chat_id, inbound_event_id, limit)
        errors = await self.latest_errors(conversation_id, chat_id, inbound_event_id, limit)

        inbound_summary = {
            "total": len(inbound),
            "processed_count": sum(1 for row in inbound if row.get("processed")),
            "ignored_count": sum(1 for row in inbound if row.get("ignored")),
            "unprocessed_count": sum(1 for row in inbound if not row.get("processed") and not row.get("ignored")),
        }
        outbound_summary = {
            "total": len(outbound),
            "pending_count": sum(1 for row in outbound if row.get("status") == "PENDING"),
            "sent_count": sum(1 for row in outbound if row.get("status") == "SENT"),
            "failed_count": sum(1 for row in outbound if str(row.get("status") or "").startswith("FAILED")),
            "retryable_count": sum(1 for row in outbound if row.get("status") == "RETRYABLE"),
            "last_errors": [
                {"id": row.get("id"), "status": row.get("status"), "last_error": row.get("last_error")}
                for row in outbound
                if row.get("last_error")
            ][:5],
        }
        customer_count = sum(1 for row in conversation if row.get("sender_role") == "customer")
        assistant_count = sum(1 for row in conversation if row.get("sender_role") == "assistant")
        checkpoint_summary = {
            "succeeded_count": sum(1 for row in checkpoints if row.get("status") == "SUCCEEDED"),
            "failed_count": sum(1 for row in checkpoints if row.get("status") == "FAILED"),
            "latest_status": checkpoints[0]["status"] if checkpoints else None,
        }
        error_summary = {
            "error_count": len(errors),
            "latest_error": errors[0] if errors else None,
        }
        ok = (
            inbound_summary["processed_count"] > 0
            and outbound_summary["sent_count"] > 0
            and customer_count > 0
            and assistant_count > 0
            and checkpoint_summary["succeeded_count"] > 0
            and error_summary["error_count"] == 0
        )
        return {
            "inbound": inbound_summary,
            "outbound": outbound_summary,
            "conversation_messages": {
                "customer_count": customer_count,
                "assistant_count": assistant_count,
                "has_customer_assistant_pair": customer_count > 0 and assistant_count > 0,
            },
            "checkpoints": checkpoint_summary,
            "errors": error_summary,
            "overall": {
                "ok": ok,
                "reason": "faq single-text closed-loop evidence found" if ok else "missing one or more closed-loop success signals",
            },
        }

    async def _fetch_rows(self, sql: str, args: tuple) -> list[dict]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                rows = await cur.fetchall()
        return [_normalize_smoke_row(dict(row)) for row in rows]

    def _build_where(self, filters: dict[str, object | None]) -> tuple[str, tuple]:
        where_clauses = []
        args = []
        for field, value in filters.items():
            if value is not None:
                where_clauses.append(f"{field} = %s")
                args.append(value)
        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        return where_sql, tuple(args)


class ExternalResultTransactionRepository:
    def __init__(
        self,
        pool,
        conversation_repository: ConversationRepository | None = None,
        outbound_repository: OutboundMessageRepository | None = None,
        external_command_repository: ExternalCommandRepository | None = None,
        result_repository: ExternalCommandResultRepository | None = None,
        conversation_message_repository: ConversationMessageRepository | None = None,
    ) -> None:
        self.pool = pool
        self.conversation_repository = conversation_repository or ConversationRepository(pool)
        self.outbound_repository = outbound_repository or OutboundMessageRepository(pool)
        self.external_command_repository = external_command_repository or ExternalCommandRepository(pool)
        self.result_repository = result_repository or ExternalCommandResultRepository(pool)
        self.conversation_message_repository = conversation_message_repository or ConversationMessageRepository(pool)

    async def process_result_transactionally(
        self,
        result: dict,
        graph_state: dict,
        outbound_messages: list[dict],
        external_commands: list[dict] | None = None,
        summary_message: dict | None = None,
    ) -> dict:
        external_commands = external_commands or []
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                conversation = await self.conversation_repository.get_or_create_on_connection(
                    conn,
                    chat_id=result["chat_id"],
                    thread_id=result.get("thread_id"),
                )
                message_insert = None
                if summary_message is not None:
                    summary_message["conversation_id"] = conversation["conversation_id"]
                    message_insert = await self.conversation_message_repository.insert_idempotent_on_connection(conn, summary_message)
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
                    external_command_inserts.append(
                        await self.external_command_repository.insert_idempotent_on_connection(conn, command)
                    )

                await self.result_repository.mark_processed_on_connection(conn, result["id"])
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return {
            "conversation": conversation,
            "message_insert": message_insert,
            "outbound_inserts": outbound_inserts,
            "external_command_inserts": external_command_inserts,
        }


class SenderTransactionRepository:
    def __init__(
        self,
        pool,
        outbound_repository: OutboundMessageRepository | None = None,
        conversation_message_repository: ConversationMessageRepository | None = None,
    ) -> None:
        self.pool = pool
        self.outbound_repository = outbound_repository or OutboundMessageRepository(pool)
        self.conversation_message_repository = conversation_message_repository or ConversationMessageRepository(pool)

    async def mark_sent_with_message(self, outbound_message_id: int, message_record: dict) -> dict:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                await self.outbound_repository.mark_sent_on_connection(conn, outbound_message_id)
                message_insert = await self.conversation_message_repository.insert_idempotent_on_connection(conn, message_record)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return {"message_insert": message_insert}


def json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def json_loads(payload) -> dict:
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _outbound_dedup_key(message: dict) -> str:
    if message.get("dedup_key"):
        return message["dedup_key"]
    tenant_id = message.get("tenant_id") or "default"
    conversation_id = message.get("conversation_id") or ""
    inbound_event_id = message.get("inbound_event_id") or ""
    action_type = message["action_type"]
    return f"{tenant_id}:{conversation_id}:{inbound_event_id}:{action_type}"


def _extract_payload_text(payload: dict | list | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("text"),
        (payload.get("event") or {}).get("text") if isinstance(payload.get("event"), dict) else None,
        (payload.get("payload") or {}).get("text") if isinstance(payload.get("payload"), dict) else None,
        (payload.get("message") or {}).get("text") if isinstance(payload.get("message"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate[:500]
    return None


def _chat_id_from_conversation_id(conversation_id: str | None) -> str | None:
    if conversation_id and conversation_id.startswith("livechat:"):
        return conversation_id.removeprefix("livechat:")
    return None


def _conversation_id_from_chat_id(chat_id: str | None) -> str | None:
    if chat_id:
        return f"livechat:{chat_id}"
    return None


def _normalize_smoke_scope(conversation_id: str | None, chat_id: str | None) -> tuple[str | None, str | None]:
    effective_chat_id = chat_id or _chat_id_from_conversation_id(conversation_id)
    effective_conversation_id = conversation_id or _conversation_id_from_chat_id(effective_chat_id)
    return effective_conversation_id, effective_chat_id


def _normalize_smoke_row(row: dict) -> dict:
    normalized = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            normalized[key] = value.strftime("%Y-%m-%d %H:%M:%S.%f")
        else:
            normalized[key] = value
    return normalized


def _sanitize_checkpoint_metadata(metadata: dict) -> dict:
    sensitive_tokens = ("token", "access_token", "secret", "api_key", "password")
    return _sanitize_metadata_value(metadata, sensitive_tokens)


def _sanitize_metadata_value(value, sensitive_tokens: tuple[str, ...]):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in sensitive_tokens):
                continue
            sanitized[key] = _sanitize_metadata_value(item, sensitive_tokens)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_metadata_value(item, sensitive_tokens) for item in value[:20]]
    if isinstance(value, str):
        return value[:500]
    return value


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


def build_external_command_result_dedup_key(
    tenant_id: str,
    conversation_id: str,
    external_command_id: int,
    command_type: str,
    result_type: str,
    result: dict,
) -> str:
    raw_result = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result_hash = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
    return f"{tenant_id}:{conversation_id}:{external_command_id}:{command_type}:{result_type}:{result_hash}"


class GatewayTransactionRepository:
    def __init__(
        self,
        pool,
        inbound_repository: InboundEventRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
        outbound_repository: OutboundMessageRepository | None = None,
        external_command_repository: ExternalCommandRepository | None = None,
        conversation_message_repository: ConversationMessageRepository | None = None,
    ) -> None:
        self.pool = pool
        self.inbound_repository = inbound_repository or InboundEventRepository(pool)
        self.conversation_repository = conversation_repository or ConversationRepository(pool)
        self.outbound_repository = outbound_repository or OutboundMessageRepository(pool)
        self.external_command_repository = external_command_repository or ExternalCommandRepository(pool)
        self.conversation_message_repository = conversation_message_repository or ConversationMessageRepository(pool)

    async def process_event_transactionally(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        customer_message: dict | None,
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
                message_insert = None
                if customer_message is not None:
                    customer_message["conversation_id"] = conversation["conversation_id"]
                    message_insert = await self.conversation_message_repository.insert_idempotent_on_connection(conn, customer_message)
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
            "message_insert": message_insert,
            "outbound_insert": outbound_inserts[0] if len(outbound_inserts) == 1 else None,
            "outbound_inserts": outbound_inserts,
            "external_command_inserts": external_command_inserts,
        }
