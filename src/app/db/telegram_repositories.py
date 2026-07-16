import hashlib
import json
from typing import Any

import aiomysql

from app.services.telegram_case_status import normalize_legacy_case_status


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TelegramCaseRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def sync_recent_external_results(self, limit: int = 200) -> dict:
        sql = """
        SELECT id, external_command_id, tenant_id, conversation_id, chat_id, thread_id,
               inbound_event_id, command_type, result_type, result_json, status, dedup_key
        FROM external_command_results
        WHERE result_type IN ('telegram.case.created', 'telegram.append_to_case.result')
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        synced = 0
        skipped = 0
        for row in reversed(rows):
            row["result_json"] = json_loads(row.get("result_json"))
            result = await self.upsert_from_external_result(row)
            if result is None:
                skipped += 1
            else:
                synced += 1
        return {"synced": synced, "skipped": skipped, "scanned": len(rows)}

    async def upsert_from_external_result(self, row: dict) -> dict | None:
        result_type = row.get("result_type")
        result_json = row.get("result_json") or {}
        if result_type == "telegram.case.created":
            return await self.upsert_case_created(row, result_json)
        if result_type == "telegram.append_to_case.result":
            return await self.record_append_message(row, result_json)
        return None

    async def upsert_case_created(self, row: dict, result_json: dict) -> dict:
        root_message_id = result_json.get("telegram_message_id")
        target_chat_id = result_json.get("target_chat_id")
        if root_message_id is None or target_chat_id is None:
            raise ValueError("telegram.case.created result missing target_chat_id or telegram_message_id")
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                case_id = await self._upsert_case_created_on_connection(conn, row, result_json)
                await self._insert_case_message_on_connection(
                    conn,
                    telegram_case_id=case_id,
                    telegram_chat_id=str(target_chat_id),
                    telegram_message_thread_id=result_json.get("message_thread_id"),
                    telegram_message_id=int(root_message_id),
                    message_kind="root",
                )
                for message_id in _attachment_message_ids(result_json):
                    await self._insert_case_message_on_connection(
                        conn,
                        telegram_case_id=case_id,
                        telegram_chat_id=str(target_chat_id),
                        telegram_message_thread_id=result_json.get("message_thread_id"),
                        telegram_message_id=int(message_id),
                        message_kind="attachment",
                    )
                await conn.commit()
                return {"telegram_case_id": case_id, "root_message_id": int(root_message_id)}
            except Exception:
                await conn.rollback()
                raise

    async def record_append_message(self, row: dict, result_json: dict) -> dict | None:
        message_id = result_json.get("telegram_message_id")
        target_chat_id = result_json.get("target_chat_id")
        if message_id is None or target_chat_id is None:
            return None
        case = await self.find_by_reply_message(
            telegram_chat_id=str(target_chat_id),
            reply_to_message_id=result_json.get("reply_to_message_id") or result_json.get("telegram_message_id"),
            message_thread_id=result_json.get("message_thread_id"),
        )
        if not case:
            return None
        async with self.pool.acquire() as conn:
            if result_json.get("status") != "edited":
                await self._insert_case_message_on_connection(
                    conn,
                    telegram_case_id=case["id"],
                    telegram_chat_id=str(target_chat_id),
                    telegram_message_thread_id=result_json.get("message_thread_id"),
                    telegram_message_id=int(message_id),
                    message_kind="append",
                )
            for attachment_id in _attachment_message_ids(result_json):
                await self._insert_case_message_on_connection(
                    conn,
                    telegram_case_id=case["id"],
                    telegram_chat_id=str(target_chat_id),
                    telegram_message_thread_id=result_json.get("message_thread_id"),
                    telegram_message_id=int(attachment_id),
                    message_kind="attachment",
                )
        return {"telegram_case_id": case["id"], "telegram_message_id": int(message_id)}

    async def find_by_reply_message(
        self,
        telegram_chat_id: str,
        reply_to_message_id: int | str | None,
        message_thread_id: int | str | None = None,
    ) -> dict | None:
        if reply_to_message_id is None:
            return None
        sql = """
        SELECT c.id, c.tenant_id,
               COALESCE(c.current_conversation_id, c.conversation_id) AS conversation_id,
               c.chat_id,
               COALESCE(c.current_thread_id, c.thread_id) AS thread_id,
               c.inbound_event_id, c.external_command_id, c.intent, c.active_workflow,
               c.telegram_chat_id, c.telegram_message_thread_id, c.root_message_id, c.status,
               s.slot_memory
        FROM telegram_case_messages m
        JOIN telegram_cases c ON c.id = m.telegram_case_id
        LEFT JOIN conversation_states s
          ON s.conversation_id = COALESCE(c.current_conversation_id, c.conversation_id)
        WHERE m.telegram_chat_id = %s
          AND m.telegram_message_id = %s
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (str(telegram_chat_id), int(reply_to_message_id)))
                row = await cur.fetchone()
        if not row:
            return None
        expected_thread = row.get("telegram_message_thread_id")
        if expected_thread is not None and message_thread_id is not None and int(expected_thread) != int(message_thread_id):
            return None
        row = dict(row)
        slot_memory = json_loads(row.pop("slot_memory", None)) or {}
        row["reply_language"] = _reply_language_from_slot_memory(slot_memory)
        return dict(row)

    async def list_money_case_candidates(
        self,
        tenant_id: str,
        chat_id: str,
        source_thread_id: str,
    ) -> list[dict]:
        sql = """
        SELECT c.id, c.tenant_id, c.conversation_id, c.current_conversation_id,
               c.chat_id, c.thread_id, c.current_thread_id, c.intent, c.active_workflow,
               c.telegram_chat_id, c.telegram_message_thread_id, c.root_message_id,
               c.status, c.created_at, c.updated_at, s.slot_memory
        FROM telegram_cases c
        LEFT JOIN conversation_states s ON s.conversation_id = c.conversation_id
        WHERE c.tenant_id = %s
          AND c.chat_id = %s
          AND COALESCE(c.current_thread_id, c.thread_id, '') <> %s
          AND c.intent IN ('deposit_missing', 'withdrawal_missing')
          AND c.status NOT IN ('completed_confirmed_by_customer', 'terminal_other')
        ORDER BY c.updated_at DESC, c.id DESC
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (tenant_id, chat_id, source_thread_id))
                rows = await cur.fetchall()
                candidates = []
                legacy_updates = []
                for raw in rows:
                    row = dict(raw)
                    row["slot_memory"] = json_loads(row.get("slot_memory")) or {}
                    normalized_status = normalize_legacy_case_status(row)
                    if str(row.get("status") or "") in {"", "created"}:
                        legacy_updates.append((normalized_status, int(row["id"])))
                    row["status"] = normalized_status
                    candidates.append(row)
                if legacy_updates:
                    await cur.executemany(
                        "UPDATE telegram_cases SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND status = 'created'",
                        legacy_updates,
                    )
        return candidates

    async def reserve_followup(
        self,
        *,
        external_command_id: int,
        telegram_case_id: int,
        source_conversation_id: str,
        source_thread_id: str,
        follow_up_kind: str,
        previous_status: str,
    ) -> dict:
        if not source_thread_id:
            raise ValueError("source_thread_id is required")
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                case = await self._lock_case_on_connection(conn, telegram_case_id)
                if not case:
                    raise ValueError(f"telegram case {telegram_case_id} not found")
                existing = await self._find_followup_on_connection(conn, telegram_case_id, source_thread_id)
                if existing:
                    await conn.commit()
                    return {**existing, "duplicate": True, "case": case}
                number = await self._next_followup_number_on_connection(conn, telegram_case_id)
                followup_id = await self._insert_followup_on_connection(
                    conn,
                    external_command_id,
                    telegram_case_id,
                    source_conversation_id,
                    source_thread_id,
                    follow_up_kind,
                    number,
                    previous_status,
                )
                await conn.commit()
                return {
                    "id": followup_id,
                    "follow_up_number": number,
                    "follow_up_kind": follow_up_kind,
                    "status": "reserved",
                    "duplicate": False,
                    "case": case,
                }
            except Exception:
                await conn.rollback()
                raise

    async def mark_followup_sending(self, followup_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        """
                        SELECT telegram_case_id, source_conversation_id, source_thread_id
                        FROM telegram_case_followups WHERE id = %s FOR UPDATE
                        """,
                        (followup_id,),
                    )
                    followup = await cur.fetchone()
                    if not followup:
                        raise ValueError(f"telegram follow-up {followup_id} not found")
                    await cur.execute(
                        "UPDATE telegram_case_followups SET status = 'sending', last_error = NULL WHERE id = %s",
                        (followup_id,),
                    )
                await self._update_current_route_on_connection(
                    conn,
                    int(followup["telegram_case_id"]),
                    str(followup["source_conversation_id"]),
                    str(followup["source_thread_id"]),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def record_followup_sent(
        self,
        followup_id: int,
        telegram_message_id: int,
        attachment_message_ids: list[int],
        customer_update_en: str,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        """
                        SELECT f.telegram_case_id, c.telegram_chat_id, c.telegram_message_thread_id
                        FROM telegram_case_followups f
                        JOIN telegram_cases c ON c.id = f.telegram_case_id
                        WHERE f.id = %s FOR UPDATE
                        """,
                        (followup_id,),
                    )
                    row = await cur.fetchone()
                    if not row:
                        raise ValueError(f"telegram follow-up {followup_id} not found")
                    await cur.execute(
                        """
                        UPDATE telegram_case_followups
                        SET status = 'sent', telegram_message_id = %s, customer_update_en = %s,
                            sent_at = NOW(6), last_error = NULL
                        WHERE id = %s
                        """,
                        (telegram_message_id, customer_update_en, followup_id),
                    )
                await self._insert_case_message_on_connection(
                    conn,
                    telegram_case_id=int(row["telegram_case_id"]),
                    telegram_chat_id=str(row["telegram_chat_id"]),
                    telegram_message_thread_id=row.get("telegram_message_thread_id"),
                    telegram_message_id=int(telegram_message_id),
                    message_kind="follow_up",
                )
                for message_id in attachment_message_ids:
                    await self._insert_case_message_on_connection(
                        conn,
                        telegram_case_id=int(row["telegram_case_id"]),
                        telegram_chat_id=str(row["telegram_chat_id"]),
                        telegram_message_thread_id=row.get("telegram_message_thread_id"),
                        telegram_message_id=int(message_id),
                        message_kind="follow_up_attachment",
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def mark_followup_delivery_uncertain(self, followup_id: int, error: str) -> None:
        await self._update_followup(
            followup_id,
            """
            UPDATE telegram_case_followups
            SET status = 'delivery_uncertain', last_error = %s
            WHERE id = %s
            """,
            (str(error)[:2000], followup_id),
        )

    async def mark_followup_retryable(self, followup_id: int, error: str) -> None:
        await self._update_followup(
            followup_id,
            "UPDATE telegram_case_followups SET status = 'reserved', last_error = %s WHERE id = %s",
            (str(error)[:2000], followup_id),
        )

    async def cancel_followup(self, followup_id: int, reason: str) -> None:
        await self._update_followup(
            followup_id,
            "UPDATE telegram_case_followups SET status = 'canceled', last_error = %s WHERE id = %s",
            (str(reason)[:2000], followup_id),
        )

    async def update_case_status_on_connection(self, conn, telegram_case_id: int, status: str) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE telegram_cases SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (status, telegram_case_id),
            )

    async def _update_followup(self, followup_id: int, sql: str, args: tuple) -> None:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)

    async def _lock_case_on_connection(self, conn, telegram_case_id: int) -> dict | None:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM telegram_cases WHERE id = %s FOR UPDATE", (telegram_case_id,))
            return await cur.fetchone()

    async def _find_followup_on_connection(self, conn, telegram_case_id: int, source_thread_id: str) -> dict | None:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT * FROM telegram_case_followups
                WHERE telegram_case_id = %s AND source_thread_id = %s
                LIMIT 1
                """,
                (telegram_case_id, source_thread_id),
            )
            return await cur.fetchone()

    async def _next_followup_number_on_connection(self, conn, telegram_case_id: int) -> int:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COALESCE(MAX(follow_up_number), 1) + 1 AS next_number "
                "FROM telegram_case_followups WHERE telegram_case_id = %s",
                (telegram_case_id,),
            )
            row = await cur.fetchone()
            return int((row or {}).get("next_number") or 2)

    async def _insert_followup_on_connection(
        self,
        conn,
        external_command_id: int,
        telegram_case_id: int,
        source_conversation_id: str,
        source_thread_id: str,
        follow_up_kind: str,
        follow_up_number: int,
        previous_status: str,
    ) -> int:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO telegram_case_followups (
                  telegram_case_id, external_command_id, source_conversation_id, source_thread_id,
                  follow_up_kind, follow_up_number, previous_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    telegram_case_id,
                    external_command_id,
                    source_conversation_id,
                    source_thread_id,
                    follow_up_kind,
                    follow_up_number,
                    previous_status,
                ),
            )
            return int(cur.lastrowid)

    async def _update_current_route_on_connection(
        self,
        conn,
        telegram_case_id: int,
        source_conversation_id: str,
        source_thread_id: str,
    ) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE telegram_cases
                SET current_conversation_id = %s, current_thread_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (source_conversation_id, source_thread_id, telegram_case_id),
            )

    async def record_staff_reply_message(
        self,
        telegram_case_id: int,
        telegram_chat_id: str,
        telegram_message_id: int,
        message_thread_id: int | None = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await self._insert_case_message_on_connection(
                conn,
                telegram_case_id=telegram_case_id,
                telegram_chat_id=str(telegram_chat_id),
                telegram_message_thread_id=message_thread_id,
                telegram_message_id=int(telegram_message_id),
                message_kind="staff_reply",
            )

    async def _upsert_case_created_on_connection(self, conn, row: dict, result_json: dict) -> int:
        sql = """
        INSERT INTO telegram_cases (
          tenant_id, conversation_id, chat_id, thread_id, inbound_event_id, external_command_id,
          intent, active_workflow, telegram_chat_id, telegram_message_thread_id, root_message_id, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'awaiting_review')
        ON DUPLICATE KEY UPDATE
          id = LAST_INSERT_ID(id),
          status = CASE WHEN status = 'created' THEN 'awaiting_review' ELSE status END,
          active_workflow = COALESCE(VALUES(active_workflow), active_workflow),
          updated_at = CURRENT_TIMESTAMP
        """
        args = (
            row.get("tenant_id") or "default",
            row["conversation_id"],
            row["chat_id"],
            row.get("thread_id"),
            row.get("inbound_event_id"),
            row.get("external_command_id") or 0,
            result_json.get("intent"),
            result_json.get("active_workflow") or result_json.get("intent"),
            str(result_json["target_chat_id"]),
            result_json.get("message_thread_id"),
            int(result_json["telegram_message_id"]),
        )
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return int(cur.lastrowid)

    async def _insert_case_message_on_connection(
        self,
        conn,
        telegram_case_id: int,
        telegram_chat_id: str,
        telegram_message_thread_id: int | str | None,
        telegram_message_id: int,
        message_kind: str,
    ) -> None:
        sql = """
        INSERT INTO telegram_case_messages (
          telegram_case_id, telegram_chat_id, telegram_message_thread_id, telegram_message_id, message_kind
        ) VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE id = id
        """
        async with conn.cursor() as cur:
            await cur.execute(
                sql,
                (
                    telegram_case_id,
                    str(telegram_chat_id),
                    int(telegram_message_thread_id) if telegram_message_thread_id is not None else None,
                    int(telegram_message_id),
                    message_kind,
                ),
            )


class TelegramUpdateOffsetRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get_offset(self, offset_key: str) -> int:
        sql = "SELECT last_update_id FROM telegram_update_offsets WHERE offset_key = %s"
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (offset_key,))
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def save_offset(self, offset_key: str, last_update_id: int) -> None:
        sql = """
        INSERT INTO telegram_update_offsets (offset_key, last_update_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE last_update_id = GREATEST(last_update_id, VALUES(last_update_id))
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (offset_key, int(last_update_id)))


def build_telegram_staff_reply_dedup_key(update: dict, case: dict) -> str:
    raw = json_dumps(
        {
            "telegram_case_id": case.get("id"),
            "telegram_update_id": update.get("update_id"),
            "telegram_message_id": (update.get("message") or {}).get("message_id"),
        }
    )
    return f"telegram.staff_reply:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def json_loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _attachment_message_ids(result_json: dict) -> list[int]:
    ids = []
    for item in result_json.get("attachment_results") or []:
        message_id = None
        if isinstance(item, dict):
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            message_id = item.get("message_id") or result.get("message_id")
        if message_id is not None:
            ids.append(int(message_id))
    return ids


def _reply_language_from_slot_memory(slot_memory: dict) -> str | None:
    for key in ("last_reply_language", "conversation_language", "last_user_language"):
        value = str((slot_memory or {}).get(key) or "").strip()
        if value:
            return value
    return None
