import argparse
import asyncio
import json
import logging
import os
import socket
import time

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ExternalCommandRepository, ExternalCommandResultRepository


SUPPORTED_COMMAND_TYPES = {
    "telegram.send_case_card",
    "telegram.append_to_case",
    "backend.query",
    "pending_reply.lookup",
    "human_handoff.requested",
    "rag.placeholder",
}


logger = logging.getLogger(__name__)


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
    limit: int = 20,
    dry_run: bool = True,
    emit_result: bool = False,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> list[dict]:
    if not dry_run:
        raise ValueError("external_command_worker currently supports --dry-run only")
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
            print(json.dumps({"dry_run": True, "command": command}, ensure_ascii=False, default=str))
            result_insert = None
            if emit_result:
                result_type, result_json = MOCK_RESULT_BY_COMMAND_TYPE[command_type]
                result_insert = await result_repository.insert_idempotent(
                    {
                        "external_command_id": command["id"],
                        "tenant_id": command.get("tenant_id") or "default",
                        "conversation_id": command["conversation_id"],
                        "chat_id": command["chat_id"],
                        "thread_id": command.get("thread_id"),
                        "inbound_event_id": command.get("inbound_event_id"),
                        "command_type": command_type,
                        "result_type": result_type,
                        "result_json": result_json,
                    }
                )
            await repository.mark_dry_run_done(command["id"])
            item = {"id": command["id"], "command_type": command_type, "status": "DRY_RUN_DONE"}
            if emit_result:
                item["result_insert"] = result_insert
            results.append(item)
        except Exception as exc:
            await repository.mark_processing_failed(command["id"], str(exc), max_retries=max_retries)
            results.append({"id": command["id"], "command_type": command_type, "status": "FAILED", "error": str(exc)})
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run pending external_commands.")
    parser.add_argument("--once", action="store_true", help="Run one external command batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external commands to process.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call real external systems.")
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
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    try:
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if emit_result else None
        results = await process_pending_commands(
            repository,
            result_repository=result_repository,
            limit=limit,
            dry_run=dry_run,
            emit_result=emit_result,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_retries=max_retries,
        )
        return {
            "worker": "external_command_worker",
            "mode": "once",
            "dry_run": dry_run,
            "emit_result": emit_result,
            "processed": len(results),
            "dry_run_done": sum(1 for result in results if result["status"] == "DRY_RUN_DONE"),
            "results_emitted": sum(1 for result in results if result.get("result_insert")),
            "failed": sum(1 for result in results if result["status"] == "FAILED"),
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
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
) -> None:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    last_recovered_at = None
    try:
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if emit_result else None
        await run_polling_loop(
            repository=repository,
            result_repository=result_repository,
            poll_seconds=settings.poll_seconds,
            limit=limit,
            dry_run=dry_run,
            emit_result=emit_result,
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
    emit_result: bool = False,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
    last_recovered_at: float | None = None,
    iterations: int | None = None,
    sleep=asyncio.sleep,
) -> None:
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
                limit=limit,
                dry_run=dry_run,
                emit_result=emit_result,
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
    if args.once:
        result = asyncio.run(
            run_once(
                limit=args.limit,
                dry_run=args.dry_run,
                emit_result=args.emit_result,
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
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                max_retries=args.max_retries,
                recover_interval_seconds=args.recover_interval_seconds,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
