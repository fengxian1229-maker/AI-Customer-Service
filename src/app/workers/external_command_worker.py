import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ExternalCommandRepository


SUPPORTED_COMMAND_TYPES = {
    "telegram.send_case_card",
    "telegram.append_to_case",
    "backend.query",
    "pending_reply.lookup",
    "human_handoff.requested",
    "rag.placeholder",
}


async def process_pending_commands(repository: ExternalCommandRepository, limit: int = 20, dry_run: bool = True) -> list[dict]:
    if not dry_run:
        raise ValueError("external_command_worker currently supports --dry-run only")

    commands = await repository.fetch_pending(limit=limit)
    results = []
    for command in commands:
        command_type = command["command_type"]
        if command_type not in SUPPORTED_COMMAND_TYPES:
            await repository.mark_failed(command["id"], f"unsupported command_type: {command_type}")
            results.append({"id": command["id"], "command_type": command_type, "status": "FAILED"})
            continue
        print(json.dumps({"dry_run": True, "command": command}, ensure_ascii=False, default=str))
        await repository.mark_dry_run_done(command["id"])
        results.append({"id": command["id"], "command_type": command_type, "status": "DRY_RUN_DONE"})
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run pending external_commands.")
    parser.add_argument("--once", action="store_true", help="Run one external command batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external commands to process.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call real external systems.")
    return parser


async def run_once(limit: int, dry_run: bool) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    try:
        repository = ExternalCommandRepository(pool)
        results = await process_pending_commands(repository, limit=limit, dry_run=dry_run)
        return {
            "worker": "external_command_worker",
            "mode": "once",
            "dry_run": dry_run,
            "processed": len(results),
            "dry_run_done": sum(1 for result in results if result["status"] == "DRY_RUN_DONE"),
            "failed": sum(1 for result in results if result["status"] == "FAILED"),
        }
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_once(limit=args.limit, dry_run=args.dry_run))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
