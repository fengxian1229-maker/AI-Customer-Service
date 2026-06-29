import argparse
import asyncio
import json

import aiomysql

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ConversationRepository, ExternalCommandRepository, ExternalCommandResultRepository, json_loads
from app.workers.external_command_worker import (
    HUMAN_HANDOFF_COMMAND_TYPE,
    _handoff_block_reason,
    _process_dry_run_command,
    _process_real_command,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely smoke-test one LiveChat human handoff command.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--inbound-event-id", type=int)
    scope.add_argument("--chat-id")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--consume-dry-run", action="store_true")
    parser.add_argument("--execute-human-handoff", action="store_true")
    parser.add_argument("--emit-result", action="store_true", default=True)
    return parser


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    settings = Settings() if args.execute_human_handoff else Settings(
        livechat_agent_access_token="unused-for-human-handoff-smoke",
        livechat_account_id="unused-for-human-handoff-smoke",
    )
    pool = await create_pool(settings)
    try:
        command = await _fetch_scoped_handoff_command(pool, inbound_event_id=args.inbound_event_id, chat_id=args.chat_id)
        if not command:
            return _summary(
                args=args,
                settings=settings,
                command=None,
                command_result=None,
                status_before=None,
                status_after=None,
                block_reason="no pending human_handoff.requested command found for scope",
                admin_summary=await _fetch_handoff_admin_summary(pool, chat_id=args.chat_id, conversation_id=None),
            )

        status_before = await _fetch_conversation_status(pool, command["conversation_id"])
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if args.emit_result else None
        conversation_repository = ConversationRepository(pool)
        if args.execute_human_handoff:
            command_result = await _process_real_command(
                command,
                repository=repository,
                result_repository=result_repository,
                conversation_repository=conversation_repository,
                emit_result=args.emit_result,
                execute_human_handoff=True,
                settings=settings,
                sender_client_factory=None,
            )
        elif args.consume_dry_run:
            command_result = await _process_dry_run_command(
                command,
                repository=repository,
                result_repository=result_repository,
                emit_result=args.emit_result,
            )
        else:
            command_result = _plan_only_result(command, settings)
        status_after = await _fetch_conversation_status(pool, command["conversation_id"])
        block_reason = None if command_result.get("status") in {"SENT", "DRY_RUN_DONE"} else command_result.get("error")
        return _summary(
            args=args,
            settings=settings,
            command=command,
            command_result=command_result,
            status_before=status_before,
            status_after=status_after,
            block_reason=block_reason,
            admin_summary=await _fetch_handoff_admin_summary(
                pool,
                chat_id=command.get("chat_id") or args.chat_id,
                conversation_id=command.get("conversation_id"),
            ),
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def _fetch_scoped_handoff_command(pool, inbound_event_id: int | None, chat_id: str | None) -> dict | None:
    where = ["command_type = %s", "status IN ('PENDING', 'RETRYABLE')"]
    params: list[object] = [HUMAN_HANDOFF_COMMAND_TYPE]
    if inbound_event_id is not None:
        where.append("inbound_event_id = %s")
        params.append(inbound_event_id)
    if chat_id is not None:
        where.append("chat_id = %s")
        params.append(chat_id)
    sql = f"""
    SELECT id, tenant_id, conversation_id, chat_id, thread_id, inbound_event_id,
           command_type, payload_json, status, retry_count, last_error,
           leased_at, lease_expires_at, locked_by, attempted_at, processed_at,
           dedup_key
    FROM external_commands
    WHERE {' AND '.join(where)}
    ORDER BY created_at ASC
    LIMIT 1
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, tuple(params))
            row = await cur.fetchone()
    if not row:
        return None
    row["payload_json"] = json_loads(row["payload_json"])
    return row


async def _fetch_conversation_status(pool, conversation_id: str) -> str | None:
    sql = "SELECT status FROM conversation_states WHERE conversation_id = %s LIMIT 1"
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, (conversation_id,))
            row = await cur.fetchone()
    return row.get("status") if row else None


async def _fetch_handoff_admin_summary(pool, chat_id: str | None, conversation_id: str | None) -> dict:
    if not chat_id and not conversation_id:
        return {}
    try:
        conversation_id = conversation_id or f"livechat:{chat_id}"
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT conversation_id, status, active_workflow, workflow_stage
                    FROM conversation_states
                    WHERE conversation_id = %s OR chat_id = %s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (conversation_id, chat_id),
                )
                conversation = await cur.fetchone()
                await cur.execute(
                    """
                    SELECT id, command_type, status, retry_count, last_error, payload_json, created_at, updated_at
                    FROM external_commands
                    WHERE (conversation_id = %s OR chat_id = %s)
                      AND command_type = %s
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (conversation_id, chat_id, HUMAN_HANDOFF_COMMAND_TYPE),
                )
                commands = await cur.fetchall()
                await cur.execute(
                    """
                    SELECT id, external_command_id, result_type, status, result_json, created_at
                    FROM external_command_results
                    WHERE conversation_id = %s OR chat_id = %s
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (conversation_id, chat_id),
                )
                results = await cur.fetchall()
                await cur.execute(
                    """
                    SELECT id, command_type, status, payload_json, created_at
                    FROM outbound_messages
                    WHERE conversation_id = %s OR chat_id = %s
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (conversation_id, chat_id),
                )
                outbound_messages = await cur.fetchall()
                await cur.execute(
                    """
                    SELECT id, sender_role, message_type, text_content, source, created_at
                    FROM conversation_messages
                    WHERE conversation_id = %s OR chat_id = %s
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (conversation_id, chat_id),
                )
                conversation_messages = await cur.fetchall()
        for row in commands:
            row["payload_json"] = json_loads(row.get("payload_json"))
        for row in results:
            row["result_json"] = json_loads(row.get("result_json"))
        for row in outbound_messages:
            row["payload_json"] = json_loads(row.get("payload_json"))
        pending_handoff = any(row.get("status") in {"PENDING", "RETRYABLE"} for row in commands)
        return {
            "conversation": conversation,
            "recent_handoff_commands": commands,
            "recent_external_command_results": results,
            "recent_outbound_messages": outbound_messages,
            "recent_conversation_messages": conversation_messages,
            "pending_handoff_command": pending_handoff,
            "human_active": (conversation or {}).get("status") == "HUMAN_ACTIVE",
        }
    except Exception as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


def _plan_only_result(command: dict, settings: Settings) -> dict:
    block_reason = _handoff_block_reason(command, settings, True)
    stage = dict((command.get("payload_json") or {}).get("human_handoff_stage") or {})
    return {
        "id": command["id"],
        "command_type": command["command_type"],
        "status": command.get("status"),
        "plan_only": True,
        "would_send_notice": not bool(stage.get("notice_sent")),
        "would_transfer": block_reason is None,
        "error": block_reason,
    }


def _summary(
    args,
    settings: Settings,
    command: dict | None,
    command_result: dict | None,
    status_before: str | None,
    status_after: str | None,
    block_reason: str | None,
    admin_summary: dict | None = None,
) -> dict:
    command_result = command_result or {}
    transfer_attempted = bool(args.execute_human_handoff and command and not _handoff_block_reason(command, settings, True))
    transfer_success = command_result.get("status") == "SENT"
    transfer_blocked = bool(block_reason and not transfer_success)
    smoke_success = bool(command_result.get("plan_only") and command)
    if not command_result.get("plan_only"):
        smoke_success = (command_result.get("status") == "DRY_RUN_DONE") if not args.execute_human_handoff else transfer_success
    return {
        "worker": "human_handoff_smoke",
        "smoke_success": smoke_success,
        "dry_run": not args.execute_human_handoff,
        "plan_only": bool(command_result.get("plan_only")),
        "consume_dry_run": bool(getattr(args, "consume_dry_run", False)),
        "execute_human_handoff": args.execute_human_handoff,
        "chat_id": (command or {}).get("chat_id") or args.chat_id,
        "thread_id": (command or {}).get("thread_id"),
        "target_group_id": settings.livechat_handoff_target_group_id,
        "command_id": (command or {}).get("id"),
        "transfer_attempted": transfer_attempted,
        "transfer_success": transfer_success,
        "transfer_blocked": transfer_blocked,
        "block_reason": block_reason,
        "conversation_status_before": status_before,
        "conversation_status_after": status_after,
        "external_command_status": command_result.get("status") or (command or {}).get("status"),
        "external_command_result": command_result,
        "would_send_notice": command_result.get("would_send_notice"),
        "would_transfer": command_result.get("would_transfer"),
        "admin_summary": admin_summary or {},
    }


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(run_sync(argv), ensure_ascii=False, indent=2, default=str))
    return 0


def run_sync(argv: list[str] | None = None) -> dict:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
