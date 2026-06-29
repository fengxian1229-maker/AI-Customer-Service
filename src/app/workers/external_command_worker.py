import argparse
import asyncio
import json
import logging
import os
import socket
import time

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ConversationRepository, ExternalCommandRepository, ExternalCommandResultRepository


SUPPORTED_COMMAND_TYPES = {
    "telegram.send_case_card",
    "telegram.append_to_case",
    "backend.query",
    "pending_reply.lookup",
    "human_handoff.requested",
    "rag.placeholder",
}


logger = logging.getLogger(__name__)

HUMAN_HANDOFF_NOTICE_TEXT = "我会为你转接真人客服继续协助。"
HUMAN_HANDOFF_COMMAND_TYPE = "human_handoff.requested"
HUMAN_HANDOFF_RESULT_TYPE = "human_handoff.transfer_chat.result"
NO_EXECUTION_MODE_ERROR = "must pass either --dry-run or --execute-human-handoff"
FAILED_AFTER_EXTERNAL_SUCCESS = "FAILED_AFTER_EXTERNAL_SUCCESS"


MOCK_RESULT_BY_COMMAND_TYPE = {
    "telegram.send_case_card": (
        "telegram.case.created",
        {
            "status": "created",
            "case_id": "mock_case",
            "message": "telegram.send_case_card dry-run completed",
        },
    ),
    "telegram.append_to_case": (
        "telegram.append_to_case.result",
        {
            "status": "appended",
            "message": "telegram.append_to_case dry-run completed",
        },
    ),
    "backend.query": (
        "backend.query.result",
        {
            "status": "success",
            "answer": "已收到查询请求，当前为 dry-run 模式，未连接真实后台。",
            "raw": {"mock": True},
        },
    ),
    "pending_reply.lookup": (
        "pending_reply.lookup.result",
        {
            "status": "found",
            "reply_text": "已收到查询请求，当前为 dry-run 模式，未连接真实 pending reply 查询源。",
        },
    ),
    "human_handoff.requested": (
        "human_handoff.requested.mock_result",
        {
            "status": "MOCKED",
            "message": "human_handoff.requested dry-run completed",
            "handoff_status": "REQUESTED_MOCK",
        },
    ),
    "rag.placeholder": (
        "rag.placeholder.mock_result",
        {
            "status": "MOCKED",
            "message": "rag.placeholder dry-run completed",
            "rag_status": "RAG_PLACEHOLDER_MOCK",
        },
    ),
}


async def process_pending_commands(
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None = None,
    conversation_repository: ConversationRepository | None = None,
    limit: int = 20,
    dry_run: bool = True,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    settings: Settings | None = None,
    sender_client_factory=None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> list[dict]:
    validate_execution_mode(dry_run=dry_run, execute_human_handoff=execute_human_handoff)
    if emit_result and result_repository is None:
        raise ValueError("result_repository is required when emit_result=True")

    worker_id = worker_id or default_worker_id()
    commands = await repository.lease_pending(limit=limit, worker_id=worker_id, lease_seconds=lease_seconds)
    results = []
    for command in commands:
        command_type = command["command_type"]
        try:
            if command_type not in SUPPORTED_COMMAND_TYPES:
                raise ValueError(f"unsupported command_type: {command_type}")
            if dry_run:
                item = await _process_dry_run_command(
                    command,
                    repository=repository,
                    result_repository=result_repository,
                    emit_result=emit_result,
                )
            else:
                item = await _process_real_command(
                    command,
                    repository=repository,
                    result_repository=result_repository,
                    conversation_repository=conversation_repository,
                    emit_result=emit_result,
                    execute_human_handoff=execute_human_handoff,
                    settings=settings,
                    sender_client_factory=sender_client_factory,
                    max_retries=max_retries,
                )
            results.append(item)
        except Exception as exc:
            await repository.mark_processing_failed(command["id"], str(exc), max_retries=max_retries)
            results.append({"id": command["id"], "command_type": command_type, "status": "FAILED", "error": str(exc)})
    return results


async def _process_dry_run_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    emit_result: bool,
) -> dict:
    command_type = command["command_type"]
    print(json.dumps({"dry_run": True, "command": command}, ensure_ascii=False, default=str))
    result_insert = None
    if emit_result:
        result_type, result_json = MOCK_RESULT_BY_COMMAND_TYPE[command_type]
        result_insert = await result_repository.insert_idempotent(_build_result_record(command, result_type, result_json))
    await repository.mark_dry_run_done(command["id"])
    item = {"id": command["id"], "command_type": command_type, "status": "DRY_RUN_DONE"}
    if emit_result:
        item["result_insert"] = result_insert
    return item


async def _process_real_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    conversation_repository: ConversationRepository | None,
    emit_result: bool,
    execute_human_handoff: bool,
    settings: Settings | None,
    sender_client_factory,
    max_retries: int = 3,
) -> dict:
    command_type = command["command_type"]
    if command_type != HUMAN_HANDOFF_COMMAND_TYPE:
        error = f"real execution unsupported for command_type: {command_type}"
        await _mark_command_status(repository, command["id"], "FAILED_UNSUPPORTED", error, max_retries=max_retries)
        return {"id": command["id"], "command_type": command_type, "status": "FAILED_UNSUPPORTED", "error": error}

    block_reason = _handoff_block_reason(command, settings, execute_human_handoff)
    if block_reason:
        status = "SKIPPED_DISABLED" if block_reason in {"livechat_handoff_enabled is false", "--execute-human-handoff is required"} else "FAILED_CONFIG"
        await _mark_command_status(repository, command["id"], status, block_reason, max_retries=max_retries)
        return {"id": command["id"], "command_type": command_type, "status": status, "error": block_reason}

    sender_client_factory = sender_client_factory or _build_sender_client
    sender_client = sender_client_factory(settings)
    target_group_id = settings.livechat_handoff_target_group_id
    ignore_agents_availability = settings.livechat_handoff_ignore_agents_availability
    ignore_requester_presence = settings.livechat_handoff_ignore_requester_presence
    handoff_stage = dict((command.get("payload_json") or {}).get("human_handoff_stage") or {})

    if handoff_stage.get("transfer_succeeded"):
        error = "LiveChat transfer may have succeeded before local completion; manual verification required before retry"
        await _mark_command_status(
            repository,
            command["id"],
            FAILED_AFTER_EXTERNAL_SUCCESS,
            error,
            max_retries=max_retries,
        )
        return {"id": command["id"], "command_type": command_type, "status": FAILED_AFTER_EXTERNAL_SUCCESS, "error": error}

    try:
        if not handoff_stage.get("notice_sent"):
            await sender_client.send_text(command["chat_id"], command.get("thread_id"), HUMAN_HANDOFF_NOTICE_TEXT)
            handoff_stage["notice_sent"] = True
            await _record_handoff_stage(repository, command["id"], handoff_stage)
        handoff_stage["transfer_attempted"] = True
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        livechat_response = await sender_client.transfer_chat_to_group(
            command["chat_id"],
            target_group_id,
            ignore_agents_availability=ignore_agents_availability,
            ignore_requester_presence=ignore_requester_presence,
        )
    except Exception as exc:
        status = classify_handoff_error(exc)
        final_status = await _mark_command_status(repository, command["id"], status, str(exc), max_retries=max_retries)
        return {"id": command["id"], "command_type": command_type, "status": final_status, "error": str(exc)}

    result_json = {
        "status": "TRANSFERRED",
        "chat_id": command["chat_id"],
        "target_group_id": target_group_id,
        "ignore_agents_availability": ignore_agents_availability,
        "ignore_requester_presence": ignore_requester_presence,
        "livechat_response": livechat_response,
    }
    result_insert = None
    try:
        handoff_stage["transfer_succeeded"] = True
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        if conversation_repository is not None:
            await conversation_repository.update_workflow_state(
                command["conversation_id"],
                {
                    "status": "HUMAN_ACTIVE",
                    "active_workflow": "human_handoff",
                    "workflow_stage": "transferred",
                    "slot_memory": {},
                },
            )
        handoff_stage["conversation_state_updated"] = True
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        if emit_result:
            result_insert = await result_repository.insert_idempotent(
                _build_result_record(command, HUMAN_HANDOFF_RESULT_TYPE, result_json, status="PROCESSED")
            )
        handoff_stage["result_emitted"] = bool(emit_result)
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        await repository.mark_sent(command["id"])
    except Exception as exc:
        error = f"LiveChat transfer may have succeeded; local handoff completion failed and manual verification is required: {exc}"
        await _mark_command_status(
            repository,
            command["id"],
            FAILED_AFTER_EXTERNAL_SUCCESS,
            error,
            max_retries=max_retries,
        )
        return {
            "id": command["id"],
            "command_type": command_type,
            "status": FAILED_AFTER_EXTERNAL_SUCCESS,
            "error": error,
            "transfer_result": result_json,
        }
    item = {"id": command["id"], "command_type": command_type, "status": "SENT", "transfer_result": result_json}
    if emit_result:
        item["result_insert"] = result_insert
    return item


def validate_execution_mode(dry_run: bool, execute_human_handoff: bool) -> None:
    if not dry_run and not execute_human_handoff:
        raise ValueError(NO_EXECUTION_MODE_ERROR)


def _handoff_block_reason(command: dict, settings: Settings | None, execute_human_handoff: bool) -> str | None:
    if not execute_human_handoff:
        return "--execute-human-handoff is required"
    if settings is None:
        return "settings are required for real human handoff"
    if not settings.livechat_handoff_enabled:
        return "livechat_handoff_enabled is false"
    if not command.get("chat_id"):
        return "command.chat_id is required"
    if settings.livechat_handoff_target_group_id is None:
        return "livechat_handoff_target_group_id is required"
    return None


def classify_handoff_error(exc: Exception) -> str:
    if isinstance(exc, LiveChatApiError):
        if exc.status in {401, 403}:
            return "FAILED_CONFIG"
        if exc.status in {429, 500, 502, 503, 504}:
            return "RETRYABLE"
        text = json.dumps(exc.data, ensure_ascii=False).lower()
        business_tokens = (
            "inactive",
            "not active",
            "closed",
            "not found",
            "requester presence",
            "group access",
        )
        if any(token in text for token in business_tokens):
            return "FAILED_BUSINESS"
        return "FAILED_UNKNOWN"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
        return "RETRYABLE"
    return "FAILED_UNKNOWN"


def _build_sender_client(settings: Settings) -> LiveChatSenderClient:
    return LiveChatSenderClient(
        settings.livechat_api_base,
        settings.livechat_account_id,
        settings.livechat_agent_access_token,
    )


def _build_result_record(command: dict, result_type: str, result_json: dict, status: str | None = None) -> dict:
    record = {
        "external_command_id": command["id"],
        "tenant_id": command.get("tenant_id") or "default",
        "conversation_id": command["conversation_id"],
        "chat_id": command["chat_id"],
        "thread_id": command.get("thread_id"),
        "inbound_event_id": command.get("inbound_event_id"),
        "command_type": command["command_type"],
        "result_type": result_type,
        "result_json": result_json,
    }
    if status is not None:
        # Real handoff transfer already applied its side effects in this worker; keep this as an audit row.
        record["status"] = status
    return record


async def _record_handoff_stage(repository: ExternalCommandRepository, command_id: int, stage: dict) -> None:
    if hasattr(repository, "merge_payload_json"):
        await repository.merge_payload_json(command_id, {"human_handoff_stage": dict(stage)})


async def _mark_command_status(
    repository: ExternalCommandRepository,
    command_id: int,
    status: str,
    error: str | None,
    max_retries: int = 3,
) -> str:
    if status == "RETRYABLE":
        if hasattr(repository, "mark_processing_failed_and_get_status"):
            return await repository.mark_processing_failed_and_get_status(command_id, error or status, max_retries=max_retries)
        if hasattr(repository, "mark_processing_failed"):
            final_status = await repository.mark_processing_failed(command_id, error or status, max_retries=max_retries)
            if isinstance(final_status, str):
                return final_status
            row = getattr(repository, "row", None)
            if isinstance(row, dict) and isinstance(row.get("status"), str):
                return row["status"]
            return status
        if hasattr(repository, "mark_retryable"):
            await repository.mark_retryable(command_id, error or status)
            return status
    if hasattr(repository, "mark_status"):
        await repository.mark_status(command_id, status, error)
        return status
    if hasattr(repository, "mark_failed"):
        await repository.mark_failed(command_id, error or status)
        return status
    raise AttributeError("repository does not support status updates")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process pending external_commands.")
    parser.add_argument("--once", action="store_true", help="Run one external command batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external commands to process.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call real external systems.")
    parser.add_argument(
        "--execute-human-handoff",
        action="store_true",
        help="Explicitly allow real LiveChat transfer_chat execution for human_handoff.requested.",
    )
    parser.add_argument("--emit-result", action="store_true", help="Emit mock external_command_results in dry-run mode.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id used for queue leases.")
    parser.add_argument("--lease-seconds", type=int, default=60, help="Seconds before a queue lease expires.")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum processing attempts before FAILED.")
    parser.add_argument(
        "--recover-interval-seconds",
        type=int,
        default=30,
        help="Seconds between expired lease recovery attempts in long-running mode. Use <= 0 to disable.",
    )
    return parser


def default_worker_id() -> str:
    return f"external-command-worker-{socket.gethostname()}-{os.getpid()}"


async def run_once(
    limit: int,
    dry_run: bool,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> dict:
    validate_execution_mode(dry_run=dry_run, execute_human_handoff=execute_human_handoff)
    settings = Settings() if execute_human_handoff else Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    try:
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if emit_result else None
        conversation_repository = ConversationRepository(pool)
        results = await process_pending_commands(
            repository,
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            limit=limit,
            dry_run=dry_run,
            emit_result=emit_result,
            execute_human_handoff=execute_human_handoff,
            settings=settings,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_retries=max_retries,
        )
        return {
            "worker": "external_command_worker",
            "mode": "once",
            "dry_run": dry_run,
            "execute_human_handoff": execute_human_handoff,
            "emit_result": emit_result,
            **summarize_results(results),
            "results": results,
        }
    finally:
        pool.close()
        await pool.wait_closed()


async def maybe_recover_expired_leases(
    repository: ExternalCommandRepository,
    last_recovered_at: float | None,
    recover_interval_seconds: int,
    now: float | None = None,
) -> float | None:
    if recover_interval_seconds <= 0:
        return last_recovered_at
    now = time.monotonic() if now is None else now
    if last_recovered_at is not None and now - last_recovered_at < recover_interval_seconds:
        return last_recovered_at
    try:
        recovered = await repository.recover_expired_leases()
        if recovered:
            logger.info("Recovered %s expired external_command leases.", recovered)
    except Exception:
        logger.exception("Failed to recover expired external_command leases.")
    return now


async def run_forever(
    limit: int,
    dry_run: bool,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
) -> None:
    validate_execution_mode(dry_run=dry_run, execute_human_handoff=execute_human_handoff)
    settings = Settings() if execute_human_handoff else Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    last_recovered_at = None
    try:
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if emit_result else None
        conversation_repository = ConversationRepository(pool)
        await run_polling_loop(
            repository=repository,
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            poll_seconds=settings.poll_seconds,
            limit=limit,
            dry_run=dry_run,
            emit_result=emit_result,
            execute_human_handoff=execute_human_handoff,
            settings=settings,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_retries=max_retries,
            recover_interval_seconds=recover_interval_seconds,
            last_recovered_at=last_recovered_at,
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def run_polling_loop(
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    poll_seconds: int,
    limit: int,
    dry_run: bool,
    conversation_repository: ConversationRepository | None = None,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    settings: Settings | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
    last_recovered_at: float | None = None,
    iterations: int | None = None,
    sleep=asyncio.sleep,
) -> None:
    validate_execution_mode(dry_run=dry_run, execute_human_handoff=execute_human_handoff)
    iteration = 0
    while iterations is None or iteration < iterations:
        last_recovered_at = await maybe_recover_expired_leases(
            repository,
            last_recovered_at=last_recovered_at,
            recover_interval_seconds=recover_interval_seconds,
        )
        try:
            await process_pending_commands(
                repository,
                result_repository=result_repository,
                conversation_repository=conversation_repository,
                limit=limit,
                dry_run=dry_run,
                emit_result=emit_result,
                execute_human_handoff=execute_human_handoff,
                settings=settings,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                max_retries=max_retries,
            )
        except Exception:
            logger.exception("external_command_worker polling iteration failed.")
        iteration += 1
        if iterations is None or iteration < iterations:
            await sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.once:
            result = asyncio.run(
                run_once(
                    limit=args.limit,
                    dry_run=args.dry_run,
                    emit_result=args.emit_result,
                    execute_human_handoff=args.execute_human_handoff,
                    worker_id=args.worker_id,
                    lease_seconds=args.lease_seconds,
                    max_retries=args.max_retries,
                )
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            asyncio.run(
                run_forever(
                    limit=args.limit,
                    dry_run=args.dry_run,
                    emit_result=args.emit_result,
                    execute_human_handoff=args.execute_human_handoff,
                    worker_id=args.worker_id,
                    lease_seconds=args.lease_seconds,
                    max_retries=args.max_retries,
                    recover_interval_seconds=args.recover_interval_seconds,
                )
            )
    except ValueError as exc:
        print(json.dumps({"worker": "external_command_worker", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    return 0


def summarize_results(results: list[dict]) -> dict:
    statuses = [result.get("status") for result in results]
    terminal_failed = sum(1 for status in statuses if isinstance(status, str) and status.startswith("FAILED"))
    retryable = sum(1 for status in statuses if status == "RETRYABLE")
    skipped = sum(1 for status in statuses if isinstance(status, str) and status.startswith("SKIPPED"))
    return {
        "processed": len(results),
        "dry_run_done": sum(1 for status in statuses if status == "DRY_RUN_DONE"),
        "sent": sum(1 for status in statuses if status == "SENT"),
        "results_emitted": sum(1 for result in results if result.get("result_insert")),
        "failed": terminal_failed,
        "terminal_failed": terminal_failed,
        "retryable": retryable,
        "skipped": skipped,
        "blocked": sum(1 for status in statuses if status == "SKIPPED_DISABLED"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
