import argparse
import asyncio
from datetime import datetime, timedelta
from typing import Any

import aiomysql

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ExternalCommandRepository, build_external_command_dedup_key, json_dumps, json_loads
from app.services.ai_failure_policy import ai_failure_handoff_notice, terminal_ai_failure_reason
from app.services.final_reply_factory import build_final_reply_service_from_settings
from app.services.user_visible_finalizer import finalize_user_visible_text


FOLLOWUP_TEXT = "请问您还在吗？如果还有问题，可以继续告诉我，我会协助您处理。"
CLOSE_TEXT = "由于暂时没有收到您的回复，本次对话将先结束。如后续仍需协助，欢迎随时重新联系我们。"
AI_FAILURE_HANDOFF_OPERATION = "livechat_idle_ai_failure_handoff"
AI_FAILURE_HANDOFF_ELIGIBLE_STATUSES = {"AI_ACTIVE", "WAITING_EXTERNAL"}
DEFAULT_FOLLOWUP_SECONDS = 120
DEFAULT_CLOSE_SECONDS = 120
DEFAULT_FAILURE_BACKOFF_SECONDS = 600
DEFAULT_MAX_FOLLOWUP_BACKFILL_SECONDS = 1800

IDLE_SLOT_KEYS = {
    "idle_followup_sent_at",
    "idle_followup_failed_at",
    "idle_followup_last_error",
    "idle_close_sent_at",
    "idle_closed_at",
    "idle_base_assistant_message_id",
    "idle_base_assistant_created_at",
    "idle_followup_message_id",
    "idle_close_message_id",
    "idle_close_failed_at",
    "idle_close_last_error",
    "idle_last_failed_at",
}


class LiveChatIdleTimerRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def fetch_candidates(self, limit: int = 20) -> list[dict]:
        sql = """
        SELECT c.conversation_id, c.tenant_id, c.channel_type, c.chat_id,
               c.current_thread_id AS thread_id, c.status, c.active_workflow,
               c.slot_memory,
               m.id AS latest_message_id,
               m.sender_role AS latest_sender_role,
               m.created_at AS latest_created_at
        FROM conversation_states c
        JOIN conversation_messages m
          ON m.id = (
            SELECT cm.id
            FROM conversation_messages cm
            WHERE cm.conversation_id = c.conversation_id
            ORDER BY cm.created_at DESC, cm.id DESC
            LIMIT 1
          )
        WHERE c.channel_type = 'livechat'
          AND c.status IN ('AI_ACTIVE', 'WAITING_EXTERNAL')
          AND COALESCE(c.active_workflow, '') <> 'human_handoff'
          AND m.sender_role = 'assistant'
          AND (
            c.status <> 'WAITING_EXTERNAL'
            OR (
              NOT EXISTS (
                SELECT 1
                FROM external_commands ec
                WHERE ec.conversation_id = c.conversation_id
                  AND ec.status IN ('PENDING', 'RETRYABLE')
              )
              AND NOT EXISTS (
                SELECT 1
                FROM external_command_results ecr
                WHERE ecr.conversation_id = c.conversation_id
                  AND ecr.status IN ('PENDING', 'RETRYABLE')
              )
              AND NOT EXISTS (
                SELECT 1
                FROM outbound_messages om
                WHERE om.conversation_id = c.conversation_id
                  AND om.status IN ('PENDING', 'RETRYABLE')
              )
            )
          )
        ORDER BY m.created_at ASC, m.id ASC
        LIMIT %s
        """
        query_limit = max(int(limit or 20) * 5, int(limit or 20))
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (query_limit,))
                rows = list(await cur.fetchall())
        candidates = []
        now = datetime.now()
        for row in rows:
            row["slot_memory"] = json_loads(row.get("slot_memory") or "{}")
            if _idle_failure_backoff_active(row["slot_memory"], now):
                continue
            candidates.append(row)
            if len(candidates) >= limit:
                break
        return candidates

    async def fetch_latest_message(self, conversation_id: str) -> dict | None:
        sql = """
        SELECT id, sender_role, message_type, text_content, source, created_at
        FROM conversation_messages
        WHERE conversation_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id,))
                return await cur.fetchone()

    async def fetch_message_created_at(self, conversation_id: str, message_id: int) -> datetime | None:
        sql = """
        SELECT created_at
        FROM conversation_messages
        WHERE conversation_id = %s
          AND id = %s
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id, message_id))
                row = await cur.fetchone()
        return _parse_datetime((row or {}).get("created_at"))

    async def has_unfinished_work(self, conversation_id: str) -> bool:
        sql = """
        SELECT 1
        FROM external_commands ec
        WHERE ec.conversation_id = %s
          AND ec.status IN ('PENDING', 'RETRYABLE')
        UNION ALL
        SELECT 1
        FROM external_command_results ecr
        WHERE ecr.conversation_id = %s
          AND ecr.status IN ('PENDING', 'RETRYABLE')
        UNION ALL
        SELECT 1
        FROM outbound_messages om
        WHERE om.conversation_id = %s
          AND om.status IN ('PENDING', 'RETRYABLE')
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id, conversation_id, conversation_id))
                return await cur.fetchone() is not None

    async def has_customer_activity_after(self, conversation: dict, occurred_after: datetime) -> bool:
        sql = """
        SELECT id
        FROM inbound_events
        WHERE chat_id = %s
          AND ignored = 0
          AND sender_role IN ('external', 'customer')
          AND effective_activity_at > %s
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation.get("chat_id"), occurred_after))
                return await cur.fetchone() is not None

    async def insert_assistant_message(self, conversation: dict, text: str, now: datetime, source: str = "livechat_idle_timer") -> int | None:
        sql = """
        INSERT INTO conversation_messages (
          conversation_id, tenant_id, channel_type, chat_id, thread_id,
          sender_role, message_type, text_content, source, occurred_at, created_at
        ) VALUES (
          %s, %s, %s, %s, %s,
          'assistant', 'text', %s, %s, %s, %s
        )
        """
        args = (
            conversation["conversation_id"],
            conversation.get("tenant_id") or "default",
            conversation.get("channel_type") or "livechat",
            conversation.get("chat_id"),
            conversation.get("thread_id"),
            text,
            source,
            now,
            now,
        )
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.lastrowid

    async def update_slot_memory(self, conversation_id: str, slot_memory: dict) -> None:
        sql = """
        UPDATE conversation_states
        SET slot_memory = %s
        WHERE conversation_id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (json_dumps(slot_memory), conversation_id))

    async def mark_closed(self, conversation_id: str, slot_memory: dict) -> None:
        sql = """
        UPDATE conversation_states
        SET status = 'CLOSED',
            slot_memory = %s
        WHERE conversation_id = %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (json_dumps(slot_memory), conversation_id))

    async def request_ai_failure_handoff(
        self,
        conversation: dict,
        slot_memory: dict,
        now: datetime,
        reason: str,
    ) -> bool:
        payload = {
            "reason": "ai_service_failure",
            "source": "livechat_idle_timer",
            "failure_reason": reason,
            "operation": AI_FAILURE_HANDOFF_OPERATION,
            "handoff_ack_mode": "direct_notice",
        }
        tenant_id = conversation.get("tenant_id") or "default"
        command = {
            "tenant_id": tenant_id,
            "conversation_id": conversation["conversation_id"],
            "chat_id": conversation["chat_id"],
            "thread_id": conversation.get("thread_id"),
            "inbound_event_id": None,
            "command_type": "human_handoff.requested",
            "payload_json": payload,
            "status": "PENDING",
            "dedup_key": build_external_command_dedup_key(
                tenant_id=tenant_id,
                conversation_id=conversation["conversation_id"],
                inbound_event_id=None,
                command_type="human_handoff.requested",
                payload={"operation": AI_FAILURE_HANDOFF_OPERATION},
            ),
        }
        update_sql = """
        UPDATE conversation_states
        SET status = 'HANDOFF_REQUESTED',
            active_workflow = 'human_handoff',
            workflow_stage = 'handoff_requested',
            slot_memory = %s
        WHERE conversation_id = %s
        """
        select_sql = """
        SELECT status, active_workflow, slot_memory
        FROM conversation_states
        WHERE conversation_id = %s
        FOR UPDATE
        """
        external_commands = ExternalCommandRepository(self.pool)
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(select_sql, (conversation["conversation_id"],))
                    row = await cur.fetchone()
                    current_status = str((row or {}).get("status") or "").upper()
                    current_workflow = str((row or {}).get("active_workflow") or "").lower()
                    if (
                        not row
                        or current_status not in AI_FAILURE_HANDOFF_ELIGIBLE_STATUSES
                        or current_workflow == "human_handoff"
                    ):
                        await conn.commit()
                        return False
                    current_slot_memory = dict(json_loads(row.get("slot_memory") or "{}") or {})
                    current_slot_memory.update(
                        {
                            "ai_service_failure_handoff_at": _format_datetime(now),
                            "ai_service_failure_reason": reason,
                            "ai_service_failure_source": "livechat_idle_timer",
                        }
                    )
                    await cur.execute(
                        update_sql,
                        (json_dumps(current_slot_memory), conversation["conversation_id"]),
                    )
                await external_commands.insert_idempotent_on_connection(conn, command)
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                raise


async def process_idle_conversations(
    pool,
    sender_client,
    limit: int = 20,
    followup_seconds: int = DEFAULT_FOLLOWUP_SECONDS,
    close_seconds: int = DEFAULT_CLOSE_SECONDS,
    now: datetime | None = None,
    repository: LiveChatIdleTimerRepository | None = None,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
) -> list[dict]:
    repository = repository or LiveChatIdleTimerRepository(pool)
    now = now or datetime.now()
    candidates = await repository.fetch_candidates(limit=limit)
    results = []
    for candidate in candidates:
        results.append(
            await process_idle_conversation(
                candidate,
                repository=repository,
                sender_client=sender_client,
                followup_seconds=followup_seconds,
                close_seconds=close_seconds,
                now=now,
                final_reply_service=final_reply_service,
                llm_final_reply_enabled=llm_final_reply_enabled,
            )
        )
    return results


async def process_idle_conversation(
    conversation: dict,
    repository,
    sender_client,
    followup_seconds: int,
    close_seconds: int,
    now: datetime,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
) -> dict:
    conversation_id = conversation["conversation_id"]
    slot_memory = dict(conversation.get("slot_memory") or {})
    latest = await repository.fetch_latest_message(conversation_id)
    if not latest or latest.get("sender_role") != "assistant":
        if slot_memory.get("idle_followup_sent_at"):
            await _clear_idle_state(repository, conversation_id, slot_memory)
            return _result(conversation, "RESET_BY_CUSTOMER")
        return _result(conversation, "SKIPPED_LATEST_NOT_ASSISTANT")

    if slot_memory.get("idle_closed_at"):
        return _result(conversation, "SKIPPED_ALREADY_CLOSED")

    if slot_memory.get("ai_service_failure_handoff_at"):
        return _result(conversation, "SKIPPED_AI_FAILURE_HANDOFF")

    if _is_waiting_external(conversation) and await _has_unfinished_work(repository, conversation_id):
        return _result(conversation, "SKIPPED_PENDING_WORK")

    followup_sent_at = _parse_datetime(slot_memory.get("idle_followup_sent_at"))
    close_sent_at = _parse_datetime(slot_memory.get("idle_close_sent_at"))

    if followup_sent_at:
        has_activity, activity_error = await _has_customer_activity_after(repository, conversation, followup_sent_at)
        if activity_error is not None:
            return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED", error=str(activity_error))
        if has_activity:
            await _clear_idle_state(repository, conversation_id, slot_memory)
            followup_sent_at = None
            close_sent_at = None

    idle_cycle_cutoff = None
    if followup_sent_at or close_sent_at:
        idle_cycle_cutoff, cutoff_error = await _resolve_idle_cycle_cutoff(
            repository,
            conversation_id,
            slot_memory,
        )
        if idle_cycle_cutoff is None:
            extra = {"error": str(cutoff_error)} if cutoff_error is not None else {}
            return _result(conversation, "SKIPPED_MISSING_IDLE_CYCLE_CUTOFF", **extra)

    if close_sent_at:
        has_activity, activity_error = await _has_customer_activity_after(repository, conversation, idle_cycle_cutoff)
        if activity_error is not None:
            return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED", error=str(activity_error))
        if has_activity:
            return _result(conversation, "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY")
        return await _close_chat(
            conversation,
            repository,
            sender_client,
            slot_memory,
            now,
            idle_cycle_cutoff,
        )

    if followup_sent_at:
        if now - followup_sent_at < timedelta(seconds=close_seconds):
            return _result(conversation, "WAITING_FOR_CLOSE_TIMER")
        has_activity, activity_error = await _has_customer_activity_after(repository, conversation, idle_cycle_cutoff)
        if activity_error is not None:
            return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED", error=str(activity_error))
        if has_activity:
            return _result(conversation, "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY")
        send_result = await _send_idle_text(
            conversation,
            repository=repository,
            sender_client=sender_client,
            text=CLOSE_TEXT,
            now=now,
            slot_memory=slot_memory,
            idle_cycle_cutoff=idle_cycle_cutoff,
            sent_at_key="idle_close_sent_at",
            message_id_key="idle_close_message_id",
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=llm_final_reply_enabled,
            node_reply_template="idle_close",
        )
        if send_result["status"] != "CLOSE_TEXT_SENT":
            return send_result
        return await _close_chat(
            conversation,
            repository,
            sender_client,
            slot_memory,
            now,
            idle_cycle_cutoff,
        )

    latest_created_at = _parse_datetime(latest.get("created_at"))
    if latest_created_at is None:
        return _result(conversation, "SKIPPED_MISSING_LATEST_TIME")
    has_activity, activity_error = await _has_customer_activity_after(repository, conversation, latest_created_at)
    if activity_error is not None:
        return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED", error=str(activity_error))
    if has_activity:
        return _result(conversation, "SKIPPED_PENDING_INBOUND_CUSTOMER_REPLY")
    if now - latest_created_at > timedelta(seconds=DEFAULT_MAX_FOLLOWUP_BACKFILL_SECONDS):
        slot_memory["idle_closed_at"] = _format_datetime(now)
        slot_memory["idle_close_reason"] = "stale_idle_backlog"
        await repository.mark_closed(conversation_id, slot_memory)
        return _result(conversation, "CLOSED_STALE_BACKLOG")
    if now - latest_created_at < timedelta(seconds=followup_seconds):
        return _result(conversation, "WAITING_FOR_FOLLOWUP_TIMER")

    slot_memory["idle_base_assistant_message_id"] = int(latest["id"])
    slot_memory["idle_base_assistant_created_at"] = _format_datetime(latest_created_at)
    return await _send_idle_text(
        conversation,
        repository=repository,
        sender_client=sender_client,
        text=FOLLOWUP_TEXT,
        now=now,
        slot_memory=slot_memory,
        sent_at_key="idle_followup_sent_at",
        message_id_key="idle_followup_message_id",
        status="FOLLOWUP_SENT",
        final_reply_service=final_reply_service,
        llm_final_reply_enabled=llm_final_reply_enabled,
        node_reply_template="idle_followup",
        idle_cycle_cutoff=latest_created_at,
    )


async def _send_idle_text(
    conversation: dict,
    repository,
    sender_client,
    text: str,
    now: datetime,
    slot_memory: dict,
    sent_at_key: str,
    message_id_key: str,
    status: str = "CLOSE_TEXT_SENT",
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
    node_reply_template: str = "idle_followup",
    idle_cycle_cutoff: datetime | None = None,
) -> dict:
    finalized = await finalize_user_visible_text(
        fallback_text=text,
        final_reply_service=final_reply_service,
        llm_final_reply_enabled=llm_final_reply_enabled,
        tenant_id=conversation.get("tenant_id") or "default",
        channel_type=conversation.get("channel_type") or "livechat",
        conversation_id=conversation.get("conversation_id"),
        chat_id=conversation.get("chat_id"),
        thread_id=conversation.get("thread_id"),
        slot_memory=slot_memory,
        active_workflow=conversation.get("active_workflow"),
        workflow_stage=conversation.get("workflow_stage"),
        status=conversation.get("status"),
        node_reply_template=node_reply_template,
        reply_plan_kind=node_reply_template,
        intent=node_reply_template,
        metadata={"source": "livechat_idle_timer"},
    )
    send_text = finalized["text"] or text
    if idle_cycle_cutoff is not None:
        has_activity, activity_error = await _has_customer_activity_after(repository, conversation, idle_cycle_cutoff)
        if activity_error is not None:
            return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED", error=str(activity_error))
        if has_activity:
            return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY")
    final_reply_result = (finalized.get("state") or {}).get("final_reply_result") or {}
    failure_reason = terminal_ai_failure_reason(final_reply_result)
    if failure_reason is not None:
        slot_memory.update(
            {
                "ai_service_failure_handoff_at": _format_datetime(now),
                "ai_service_failure_reason": failure_reason,
                "ai_service_failure_source": "livechat_idle_timer",
            }
        )
        handoff_requested = await repository.request_ai_failure_handoff(conversation, slot_memory, now, failure_reason)
        if not handoff_requested:
            return _result(conversation, "SKIPPED_TERMINAL_STATE")
        try:
            await sender_client.send_text(
                chat_id=conversation["chat_id"],
                thread_id=conversation.get("thread_id"),
                text=ai_failure_handoff_notice((finalized.get("state") or {}).get("reply_language")),
            )
        except Exception as exc:
            return _result(conversation, "FAILED_HANDOFF_NOTICE", error=str(exc))
        return _result(conversation, "AI_FAILURE_HANDOFF_REQUESTED")
    try:
        await sender_client.send_text(
            chat_id=conversation["chat_id"],
            thread_id=conversation.get("thread_id"),
            text=send_text,
        )
    except Exception as exc:
        if _is_already_closed_error(exc):
            slot_memory["idle_closed_at"] = _format_datetime(now)
            slot_memory.pop("idle_close_last_error", None)
            slot_memory.pop("idle_followup_last_error", None)
            slot_memory.pop("idle_last_failed_at", None)
            await repository.mark_closed(conversation["conversation_id"], slot_memory)
            return _result(conversation, "CLOSED_ALREADY_IN_LIVECHAT", error=str(exc))
        error_key = "idle_close_last_error" if sent_at_key == "idle_close_sent_at" else "idle_followup_last_error"
        failed_at_key = "idle_close_failed_at" if sent_at_key == "idle_close_sent_at" else "idle_followup_failed_at"
        slot_memory[error_key] = str(exc)
        slot_memory[failed_at_key] = _format_datetime(now)
        slot_memory["idle_last_failed_at"] = _format_datetime(now)
        await repository.update_slot_memory(conversation["conversation_id"], slot_memory)
        return _result(conversation, "FAILED_SEND_TEXT", error=str(exc))
    message_id = await repository.insert_assistant_message(conversation, text=send_text, now=now)
    slot_memory[sent_at_key] = _format_datetime(now)
    if message_id is not None:
        slot_memory[message_id_key] = int(message_id)
    slot_memory.pop("idle_close_last_error", None)
    await repository.update_slot_memory(conversation["conversation_id"], slot_memory)
    return _result(conversation, status, message_id=message_id)


async def _close_chat(
    conversation: dict,
    repository,
    sender_client,
    slot_memory: dict,
    now: datetime,
    idle_cycle_cutoff: datetime | None,
) -> dict:
    if idle_cycle_cutoff is None:
        return _result(conversation, "SKIPPED_MISSING_IDLE_CYCLE_CUTOFF")
    has_activity, activity_error = await _has_customer_activity_after(repository, conversation, idle_cycle_cutoff)
    if activity_error is not None:
        return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY_CHECK_FAILED", error=str(activity_error))
    if has_activity:
        return _result(conversation, "SKIPPED_CUSTOMER_ACTIVITY")
    try:
        await sender_client.deactivate_chat(conversation["chat_id"])
    except Exception as exc:
        if _is_already_closed_error(exc):
            slot_memory["idle_closed_at"] = _format_datetime(now)
            slot_memory.pop("idle_close_last_error", None)
            await repository.mark_closed(conversation["conversation_id"], slot_memory)
            return _result(conversation, "CLOSED_ALREADY_IN_LIVECHAT")
        slot_memory["idle_close_last_error"] = str(exc)
        await repository.update_slot_memory(conversation["conversation_id"], slot_memory)
        return _result(conversation, "FAILED_CLOSE_CHAT", error=str(exc))
    slot_memory["idle_closed_at"] = _format_datetime(now)
    slot_memory.pop("idle_close_last_error", None)
    await repository.mark_closed(conversation["conversation_id"], slot_memory)
    return _result(conversation, "CLOSED")


async def _clear_idle_state(repository, conversation_id: str, slot_memory: dict) -> None:
    for key in IDLE_SLOT_KEYS:
        slot_memory.pop(key, None)
    await repository.update_slot_memory(conversation_id, slot_memory)


def _is_already_closed_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if isinstance(exc, LiveChatApiError) and exc.status in {400, 404, 422}:
        error = (exc.data or {}).get("error") or {}
        if str(error.get("type") or "").lower() == "chat_inactive":
            return True
        return any(
            marker in message
            for marker in ("chat is closed", "chat closed", "chat not active", "not active", "no active chat thread")
        )
    return False


def _idle_failure_backoff_active(slot_memory: dict, now: datetime) -> bool:
    failed_at = _parse_datetime((slot_memory or {}).get("idle_last_failed_at"))
    if failed_at is None:
        return False
    return now - failed_at < timedelta(seconds=DEFAULT_FAILURE_BACKOFF_SECONDS)


def _is_waiting_external(conversation: dict) -> bool:
    return str(conversation.get("status") or "").upper() == "WAITING_EXTERNAL"


async def _has_unfinished_work(repository, conversation_id: str) -> bool:
    if not hasattr(repository, "has_unfinished_work"):
        return False
    return bool(await repository.has_unfinished_work(conversation_id))


async def _has_customer_activity_after(repository, conversation: dict, occurred_after: datetime) -> tuple[bool, Exception | None]:
    try:
        return bool(await repository.has_customer_activity_after(conversation, occurred_after)), None
    except Exception as exc:
        return False, exc


async def _resolve_idle_cycle_cutoff(
    repository,
    conversation_id: str,
    slot_memory: dict,
) -> tuple[datetime | None, Exception | None]:
    persisted = _parse_datetime(slot_memory.get("idle_base_assistant_created_at"))
    if persisted is not None:
        return persisted, None
    message_id = slot_memory.get("idle_base_assistant_message_id")
    if message_id is None:
        return None, None
    try:
        recovered = _parse_datetime(await repository.fetch_message_created_at(conversation_id, int(message_id)))
        if recovered is None:
            return None, None
        slot_memory["idle_base_assistant_created_at"] = _format_datetime(recovered)
        await repository.update_slot_memory(conversation_id, slot_memory)
        return recovered, None
    except Exception as exc:
        return None, exc


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _format_datetime(value: datetime) -> str:
    return value.isoformat(sep=" ")


def _result(conversation: dict, status: str, **extra) -> dict:
    return {
        "conversation_id": conversation.get("conversation_id"),
        "chat_id": conversation.get("chat_id"),
        "status": status,
        **extra,
    }


def summarize_results(results: list[dict]) -> dict:
    first_error = next((result.get("error") for result in results if result.get("error")), None)
    return {
        "processed": len(results),
        "followup_sent": sum(1 for result in results if result.get("status") == "FOLLOWUP_SENT"),
        "close_text_sent": sum(1 for result in results if result.get("status") == "CLOSE_TEXT_SENT"),
        "closed": sum(1 for result in results if result.get("status") in {"CLOSED", "CLOSED_ALREADY_IN_LIVECHAT"}),
        "failed": sum(1 for result in results if str(result.get("status") or "").startswith("FAILED")),
        "reset": sum(1 for result in results if result.get("status") == "RESET_BY_CUSTOMER"),
        "first_error": first_error,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run LiveChat idle follow-up and auto-close timer once.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--followup-seconds", type=int, default=DEFAULT_FOLLOWUP_SECONDS)
    parser.add_argument("--close-seconds", type=int, default=DEFAULT_CLOSE_SECONDS)
    return parser


async def run_once(args: argparse.Namespace) -> dict:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        final_reply_service = build_final_reply_service_from_settings(settings)
        sender_client = LiveChatSenderClient(
            settings.livechat_api_base,
            settings.livechat_account_id,
            settings.livechat_agent_access_token,
            agent_email=getattr(settings, "livechat_agent_email", None),
        )
        results = await process_idle_conversations(
            pool,
            sender_client,
            limit=args.limit,
            followup_seconds=args.followup_seconds,
            close_seconds=args.close_seconds,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=getattr(settings, "llm_final_reply_enabled", False),
        )
        return {"worker": "livechat_idle_timer", **summarize_results(results), "results": results}
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_once(args))
    print(json_dumps(result))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
