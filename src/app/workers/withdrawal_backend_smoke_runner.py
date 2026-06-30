import argparse
import asyncio
import json
import re
from typing import Any

import aiomysql

from app.channels.livechat.sender_client import LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ConversationRepository,
    ExternalCommandRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    OutboundMessageRepository,
    json_loads,
)
from app.workers import backend_sop_smoke_admin, external_command_worker, external_result_consumer, gateway_consumer, sender_worker


WORKER_ID = "withdrawal-smoke-runner"
SAFE_BACKEND_FAILURE_TEXT = "后台查询暂时无法完成，我们会继续为你人工复核，请稍候。"


class SmokeRunnerReadRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get_inbound(self, inbound_event_id: int) -> dict[str, Any] | None:
        rows = await self._fetch_all(
            """
            SELECT id, source, raw_action, chat_id, thread_id, event_id,
                   standard_event_type, processed, ignored, ignore_reason,
                   occurred_at, created_at, payload_json
            FROM inbound_events
            WHERE id = %s
            LIMIT 1
            """,
            (inbound_event_id,),
        )
        if not rows:
            return None
        row = rows[0]
        row["payload_json"] = json_loads(row.get("payload_json"))
        return row

    async def list_backend_commands(self, inbound_event_id: int) -> list[dict[str, Any]]:
        rows = await self._fetch_all(
            """
            SELECT id, tenant_id, conversation_id, chat_id, thread_id, inbound_event_id,
                   command_type, status, retry_count, last_error, payload_json,
                   created_at, updated_at
            FROM external_commands
            WHERE inbound_event_id = %s
              AND command_type = 'backend.query'
            ORDER BY id ASC
            """,
            (inbound_event_id,),
        )
        for row in rows:
            row["payload_json"] = json_loads(row.get("payload_json"))
        return rows

    async def list_results_for_command(self, command_id: int) -> list[dict[str, Any]]:
        rows = await self._fetch_all(
            """
            SELECT id, external_command_id, tenant_id, conversation_id, chat_id, thread_id,
                   inbound_event_id, command_type, result_type, status, retry_count,
                   last_error, result_json, created_at, updated_at
            FROM external_command_results
            WHERE external_command_id = %s
              AND result_type = 'backend.query.result'
            ORDER BY id ASC
            """,
            (command_id,),
        )
        for row in rows:
            row["result_json"] = json_loads(row.get("result_json"))
        return rows

    async def _fetch_all(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return list(await cur.fetchall())


async def run_smoke(
    pool,
    inbound_event_id: int,
    repository: SmokeRunnerReadRepository | None = None,
    settings: Settings | None = None,
    plan_only: bool = False,
    dry_run: bool = False,
    execute_backend: bool = False,
    send_livechat: bool = False,
    assert_closed_loop: bool = False,
    worker_id: str = WORKER_ID,
    lease_seconds: int = 60,
) -> dict[str, Any]:
    settings = settings or Settings(
        livechat_agent_access_token="unused-for-withdrawal-smoke-runner",
        livechat_account_id="unused-for-withdrawal-smoke-runner",
        backend_query_enabled=execute_backend,
    )
    repository = repository or SmokeRunnerReadRepository(pool)
    mode = "plan_only" if plan_only else ("dry_run" if dry_run else "execute")
    result: dict[str, Any] = {
        "worker": "withdrawal_backend_smoke_runner",
        "mode": mode,
        "inbound_event_id": inbound_event_id,
        "execute_backend": bool(execute_backend),
        "send_livechat": bool(send_livechat),
        "assert_closed_loop": bool(assert_closed_loop),
        "steps": {},
        "smoke_status": "STARTED",
        "closed_loop": False,
        "safe_failure_processed": False,
        "failure_reasons": [],
    }

    inbound = await repository.get_inbound(inbound_event_id)
    result["steps"]["inbound"] = _inbound_summary(inbound)
    if not inbound:
        result["smoke_status"] = "NO_INBOUND"
        return sanitize(result)
    result["chat_id"] = inbound.get("chat_id")
    result["thread_id"] = inbound.get("thread_id")
    if inbound.get("ignored"):
        result["smoke_status"] = "INBOUND_IGNORED"
        return sanitize(result)
    if inbound.get("standard_event_type") not in {"MESSAGE_CREATED", "FILE_RECEIVED"}:
        result["smoke_status"] = "UNSUPPORTED_INBOUND_TYPE"
        return sanitize(result)

    if plan_only:
        result["steps"]["gateway"] = {"planned": bool(not inbound.get("processed")), "changed_db": False}
    elif inbound.get("processed"):
        result["steps"]["gateway"] = {"skipped": True, "reason": "inbound already processed", "changed_db": False}
    else:
        gateway_result = await gateway_consumer.process_inbound_event_id(
            pool,
            inbound_event_id=inbound_event_id,
            checkpoint_mode=settings.langgraph_checkpoint_mode,
            settings=settings,
        )
        result["steps"]["gateway"] = {**gateway_result, "changed_db": True}
        if gateway_result.get("failed"):
            result["smoke_status"] = "GATEWAY_FAILED"
            return sanitize(result)

    result["steps"]["immediate_reply"] = await _sender_step(
        pool,
        settings,
        inbound_event_id,
        send_livechat=send_livechat,
        plan_only=plan_only,
    )

    commands = await repository.list_backend_commands(inbound_event_id)
    selected = _select_backend_command(commands)
    result["steps"]["backend_command"] = {
        "found": bool(commands),
        "duplicates_count": max(0, len(commands) - 1),
        "command_id": selected.get("id") if selected else None,
        "status": selected.get("status") if selected else None,
    }
    if not selected:
        result["smoke_status"] = "SOP_NOT_TRIGGERED"
        return sanitize(result)

    if not execute_backend:
        result["steps"]["backend_execute"] = {
            "status": "PLAN_BACKEND_EXECUTION" if plan_only else "BACKEND_COMMAND_PENDING",
            "changed_db": False,
        }
        result["smoke_status"] = "PLAN_BACKEND_EXECUTION" if plan_only else "BACKEND_COMMAND_PENDING"
        return sanitize(result)

    command_repository = ExternalCommandRepository(pool)
    result_repository = ExternalCommandResultRepository(pool)
    command = await command_repository.lease_pending_by_id(selected["id"], worker_id=worker_id, lease_seconds=lease_seconds)
    if not command:
        result["steps"]["backend_execute"] = {"status": "COMMAND_LOCKED_OR_NOT_PENDING", "changed_db": False}
        result["smoke_status"] = "COMMAND_LOCKED_OR_NOT_PENDING"
        return sanitize(result)
    backend_execute = await external_command_worker._process_real_backend_query_command(
        command,
        repository=command_repository,
        result_repository=result_repository,
        emit_result=True,
        execute_backend=True,
        settings=settings,
    )
    result["steps"]["backend_execute"] = {**backend_execute, "changed_db": True}

    backend_results = await repository.list_results_for_command(command["id"])
    backend_result = backend_results[0] if backend_results else None
    if not backend_result:
        result["smoke_status"] = "BACKEND_COMMAND_SENT"
        return sanitize(result)

    consume_result = await external_result_consumer.process_result_by_id(
        result_repository=result_repository,
        conversation_repository=ConversationRepository(pool),
        outbound_repository=OutboundMessageRepository(pool),
        result_id=backend_result["id"],
        transaction_repository=_SmokeResultTransaction(
            result_repository,
            ConversationRepository(pool),
            OutboundMessageRepository(pool),
        ),
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    result["steps"]["backend_result_consume"] = consume_result
    result_json = backend_result.get("result_json") or {}
    result["safe_failure_processed"] = result_json.get("status") == "failed" and consume_result.get("status") == "PROCESSED"

    result["steps"]["backend_answer"] = await _sender_step(
        pool,
        settings,
        inbound_event_id,
        send_livechat=send_livechat,
        plan_only=False,
    )

    if not hasattr(pool, "acquire"):
        result["smoke_status"] = "BACKEND_RESULT_PROCESSED" if consume_result.get("status") == "PROCESSED" else "BACKEND_RESULT_PENDING"
        result["closed_loop"] = consume_result.get("status") == "PROCESSED"
        return sanitize(result)

    admin_repository = backend_sop_smoke_admin.BackendSopSmokeReadRepository(pool)
    admin_snapshot = await admin_repository.by_inbound(inbound_event_id)
    result["admin_snapshot"] = _admin_summary(admin_snapshot)
    if assert_closed_loop:
        assertion = backend_sop_smoke_admin.assert_closed_loop(admin_snapshot)
        result["steps"]["assert"] = assertion
        result["closed_loop"] = assertion["closed_loop"]
        result["failure_reasons"] = assertion["failure_reasons"]
        result["smoke_status"] = assertion["smoke_status"]
    else:
        result["smoke_status"] = admin_snapshot.get("smoke_status") or backend_sop_smoke_admin.infer_smoke_status(admin_snapshot)
        result["closed_loop"] = result["smoke_status"] == "BACKEND_ANSWER_SENT" and not result["safe_failure_processed"]
    if result["safe_failure_processed"]:
        result["closed_loop"] = False
    return sanitize(result)


class _SmokeResultTransaction:
    def __init__(self, result_repository, conversation_repository, outbound_repository) -> None:
        self.result_repository = result_repository
        self.conversation_repository = conversation_repository
        self.outbound_repository = outbound_repository

    async def process_result_transactionally(
        self,
        row: dict,
        graph_state: dict,
        outbound_messages: list[dict],
        external_commands: list[dict] | None = None,
        summary_message: dict | None = None,
    ) -> None:
        del external_commands, summary_message
        await self.conversation_repository.update_workflow_state(row["conversation_id"], graph_state)
        for message in outbound_messages:
            await self.outbound_repository.insert_idempotent(message)
        await self.result_repository.mark_processed(row["id"])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-inbound withdrawal backend closed-loop smoke runner.")
    parser.add_argument("--inbound-event-id", type=int, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute-backend", action="store_true")
    parser.add_argument("--send-livechat", action="store_true")
    parser.add_argument("--assert-closed-loop", action="store_true")
    parser.add_argument("--worker-id", default=WORKER_ID)
    parser.add_argument("--lease-seconds", type=int, default=60)
    return parser


async def run_cli(args) -> dict[str, Any]:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        return await run_smoke(
            pool=pool,
            inbound_event_id=args.inbound_event_id,
            settings=settings,
            plan_only=args.plan_only,
            dry_run=args.dry_run,
            execute_backend=args.execute_backend,
            send_livechat=args.send_livechat,
            assert_closed_loop=args.assert_closed_loop,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_cli(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if not result.get("failure_reasons") else 1


async def _sender_step(
    pool,
    settings: Settings,
    inbound_event_id: int,
    send_livechat: bool,
    plan_only: bool,
) -> dict[str, Any]:
    if plan_only:
        return {"planned": True, "send_livechat": False, "changed_db": False}
    if not send_livechat:
        return {"planned": True, "send_livechat": False, "changed_db": False}
    client = LiveChatSenderClient(
        settings.livechat_api_base,
        settings.livechat_account_id,
        settings.livechat_agent_access_token,
    )
    rows = await sender_worker.process_pending_for_inbound_event(pool, client, inbound_event_id=inbound_event_id)
    return {
        "send_livechat": True,
        "changed_db": True,
        "processed": len(rows),
        "sent": sum(1 for row in rows if row.get("status") == "SENT"),
        "results": rows,
    }


def _select_backend_command(commands: list[dict[str, Any]]) -> dict[str, Any] | None:
    for command in commands:
        if command.get("status") in {"PENDING", "RETRYABLE"}:
            return command
    return commands[0] if commands else None


def _inbound_summary(inbound: dict[str, Any] | None) -> dict[str, Any]:
    if not inbound:
        return {"found": False}
    payload = inbound.get("payload_json") or {}
    event = payload.get("event") if isinstance(payload, dict) else {}
    text = (event or {}).get("text") or payload.get("text") or payload.get("message") if isinstance(payload, dict) else None
    return {
        "found": True,
        "inbound_event_id": inbound.get("id"),
        "chat_id": inbound.get("chat_id"),
        "thread_id": inbound.get("thread_id"),
        "event_id": inbound.get("event_id"),
        "standard_event_type": inbound.get("standard_event_type"),
        "processed": bool(inbound.get("processed")),
        "ignored": bool(inbound.get("ignored")),
        "text_summary": str(text or "")[:160],
    }


def _admin_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    command = next((row for row in snapshot.get("external_commands") or [] if row.get("command_type") == "backend.query"), None)
    result = next((row for row in snapshot.get("external_command_results") or [] if row.get("result_type") == "backend.query.result"), None)
    outbounds = snapshot.get("outbound_messages") or []
    return {
        "smoke_status": snapshot.get("smoke_status"),
        "backend_command_id": command.get("id") if command else None,
        "backend_command_status": command.get("status") if command else None,
        "backend_result_id": result.get("id") if result else None,
        "backend_result_status": result.get("status") if result else None,
        "outbound_statuses": [row.get("status") for row in outbounds],
    }


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "<redacted>" if _is_secret_key(key) else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_text(value)
    return value


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(token in lowered for token in ("authorization", "password", "cookie", "token", "bearer"))


def _redact_secret_text(text: str) -> str:
    redacted = text
    patterns = [
        r"Authorization\s*[:=]\s*[^,\s}]+",
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        r"token\s*[:=]\s*[^,\s}]+",
        r"password\s*[:=]\s*[^,\s}]+",
        r"cookie\s*[:=]\s*[^,\s}]+",
    ]
    for pattern in patterns:
        redacted = re.sub(pattern, "<redacted>", redacted, flags=re.I)
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
