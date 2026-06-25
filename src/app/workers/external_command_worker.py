import argparse
import asyncio
import json

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


MOCK_RESULT_BY_COMMAND_TYPE = {
    "telegram.send_case_card": (
        "telegram.send_case_card.mock_result",
        {
            "status": "MOCKED",
            "message": "telegram.send_case_card dry-run completed",
            "case_status": "SENT_TO_TG_MOCK",
        },
    ),
    "telegram.append_to_case": (
        "telegram.append_to_case.mock_result",
        {
            "status": "MOCKED",
            "message": "telegram.append_to_case dry-run completed",
            "case_status": "APPENDED_TO_TG_CASE_MOCK",
        },
    ),
    "backend.query": (
        "backend.query.mock_result",
        {
            "status": "MOCKED",
            "message": "backend.query dry-run completed",
            "query_status": "BACKEND_QUERY_MOCK",
        },
    ),
    "pending_reply.lookup": (
        "pending_reply.lookup.mock_result",
        {
            "status": "MOCKED",
            "message": "pending_reply.lookup dry-run completed",
            "lookup_status": "PENDING_REPLY_LOOKUP_MOCK",
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
) -> list[dict]:
    if not dry_run:
        raise ValueError("external_command_worker currently supports --dry-run only")
    if emit_result and result_repository is None:
        raise ValueError("result_repository is required when emit_result=True")

    commands = await repository.fetch_pending(limit=limit)
    results = []
    for command in commands:
        command_type = command["command_type"]
        if command_type not in SUPPORTED_COMMAND_TYPES:
            await repository.mark_failed(command["id"], f"unsupported command_type: {command_type}")
            results.append({"id": command["id"], "command_type": command_type, "status": "FAILED"})
            continue
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
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run pending external_commands.")
    parser.add_argument("--once", action="store_true", help="Run one external command batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external commands to process.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call real external systems.")
    parser.add_argument("--emit-result", action="store_true", help="Emit mock external_command_results in dry-run mode.")
    return parser


async def run_once(limit: int, dry_run: bool, emit_result: bool = False) -> dict:
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


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_once(limit=args.limit, dry_run=args.dry_run, emit_result=args.emit_result))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
