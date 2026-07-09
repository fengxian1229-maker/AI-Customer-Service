import argparse
import asyncio
from datetime import datetime, timedelta
from typing import Any

import aiomysql

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import json_dumps, json_loads
from app.services.final_reply_factory import build_final_reply_service_from_settings
from app.services.user_visible_finalizer import finalize_user_visible_text


FOLLOWUP_TEXT = "请问您还在吗？如果还有问题，可以继续告诉我，我会协助您处理。"
CLOSE_TEXT = "由于暂时没有收到您的回复，本次对话将先结束。如后续仍需协助，欢迎随时重新联系我们。"
DEFAULT_FOLLOWUP_SECONDS = 120
DEFAULT_CLOSE_SECONDS = 120
DEFAULT_FAILURE_BACKOFF_SECONDS = 600

IDLE_SLOT_KEYS = {
    "idle_followup_sent_at",
    "idle_followup_failed_at",
    "idle_followup_last_error",
    "idle_close_sent_at",
    "idle_closed_at",
    "idle_base_assistant_message_id",
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
          AND c.status = 'AI_ACTIVE'
          AND COALESCE(c.active_workflow, '') <> 'human_handoff'
          AND m.sender_role = 'assistant'
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

    async def has_customer_message_after(self, conversation_id: str, created_at: datetime) -> bool:
        sql = """
        SELECT id
        FROM conversation_messages
        WHERE conversation_id = %s
          AND sender_role = 'customer'
          AND created_at > %s
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (conversation_id, created_at))
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

    followup_sent_at = _parse_datetime(slot_memory.get("idle_followup_sent_at"))
    close_sent_at = _parse_datetime(slot_memory.get("idle_close_sent_at"))

    if followup_sent_at and await repository.has_customer_message_after(conversation_id, followup_sent_at):
        await _clear_idle_state(repository, conversation_id, slot_memory)
        return _result(conversation, "RESET_BY_CUSTOMER")

    if close_sent_at:
        return await _close_chat(conversation, repository, sender_client, slot_memory, now)

    if followup_sent_at:
        if now - followup_sent_at < timedelta(seconds=close_seconds):
            return _result(conversation, "WAITING_FOR_CLOSE_TIMER")
        send_result = await _send_idle_text(
            conversation,
            repository=repository,
            sender_client=sender_client,
            text=CLOSE_TEXT,
            now=now,
            slot_memory=slot_memory,
            sent_at_key="idle_close_sent_at",
            message_id_key="idle_close_message_id",
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=llm_final_reply_enabled,
            node_reply_template="idle_close",
        )
        if send_result["status"] != "CLOSE_TEXT_SENT":
            return send_result
        return await _close_chat(conversation, repository, sender_client, slot_memory, now)

    latest_created_at = _parse_datetime(latest.get("created_at"))
    if latest_created_at is None:
        return _result(conversation, "SKIPPED_MISSING_LATEST_TIME")
    if now - latest_created_at < timedelta(seconds=followup_seconds):
        return _result(conversation, "WAITING_FOR_FOLLOWUP_TIMER")

    slot_memory["idle_base_assistant_message_id"] = int(latest["id"])
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


async def _close_chat(conversation: dict, repository, sender_client, slot_memory: dict, now: datetime) -> dict:
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
        return any(marker in message for marker in ("chat is closed", "chat closed", "chat not active", "not active"))
    return False


def _idle_failure_backoff_active(slot_memory: dict, now: datetime) -> bool:
    failed_at = _parse_datetime((slot_memory or {}).get("idle_last_failed_at"))
    if failed_at is None:
        return False
    return now - failed_at < timedelta(seconds=DEFAULT_FAILURE_BACKOFF_SECONDS)


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
    return value.replace(microsecond=0).isoformat(sep=" ")


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
