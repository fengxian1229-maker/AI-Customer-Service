import hashlib
import json
from typing import Any

import aiomysql


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
        SELECT c.id, c.tenant_id, c.conversation_id, c.chat_id, c.thread_id,
               c.inbound_event_id, c.external_command_id, c.intent, c.active_workflow,
               c.telegram_chat_id, c.telegram_message_thread_id, c.root_message_id, c.status,
               s.slot_memory
        FROM telegram_case_messages m
        JOIN telegram_cases c ON c.id = m.telegram_case_id
        LEFT JOIN conversation_states s ON s.conversation_id = c.conversation_id
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
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'created')
        ON DUPLICATE KEY UPDATE
          id = LAST_INSERT_ID(id),
          status = VALUES(status),
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
