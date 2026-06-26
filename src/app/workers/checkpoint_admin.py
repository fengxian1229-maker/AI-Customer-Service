import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import GraphCheckpointRunRepository, GraphRunErrorRepository


def add_filter_arguments(parser: argparse.ArgumentParser, *, include_status: bool) -> None:
    parser.add_argument("--conversation-id")
    parser.add_argument("--graph-thread-id")
    parser.add_argument("--inbound-event-id", type=int)
    if include_status:
        parser.add_argument("--status")
    parser.add_argument("--created-after")
    parser.add_argument("--created-before")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only checkpoint run and graph error admin CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_runs = subparsers.add_parser("list-runs")
    add_filter_arguments(list_runs, include_status=True)
    list_runs.add_argument("--limit", type=int, default=20)

    show_run = subparsers.add_parser("show-run")
    show_run.add_argument("--run-id", type=int, required=True)

    latest = subparsers.add_parser("latest")
    add_filter_arguments(latest, include_status=True)

    errors = subparsers.add_parser("errors")
    add_filter_arguments(errors, include_status=False)
    errors.add_argument("--limit", type=int, default=20)
    return parser


async def run_command(args, checkpoint_run_repository, graph_run_error_repository) -> dict:
    if args.command == "list-runs":
        runs = await checkpoint_run_repository.list_runs(
            conversation_id=args.conversation_id,
            graph_thread_id=args.graph_thread_id,
            inbound_event_id=args.inbound_event_id,
            status=args.status,
            created_after=args.created_after,
            created_before=args.created_before,
            limit=args.limit,
        )
        return {"command": "list-runs", "runs": runs}

    if args.command == "show-run":
        run = await checkpoint_run_repository.get_run(args.run_id)
        return {"command": "show-run", "run": run}

    if args.command == "latest":
        run = await checkpoint_run_repository.fetch_latest(
            conversation_id=args.conversation_id,
            graph_thread_id=args.graph_thread_id,
            inbound_event_id=args.inbound_event_id,
            status=args.status,
            created_after=args.created_after,
            created_before=args.created_before,
        )
        return {"command": "latest", "run": run}

    errors = await graph_run_error_repository.list_errors(
        conversation_id=args.conversation_id,
        graph_thread_id=args.graph_thread_id,
        inbound_event_id=args.inbound_event_id,
        created_after=args.created_after,
        created_before=args.created_before,
        limit=args.limit,
    )
    return {"command": "errors", "errors": errors}


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    settings = Settings(
        livechat_agent_access_token="unused-for-checkpoint-admin",
        livechat_account_id="unused-for-checkpoint-admin",
    )
    pool = await create_pool(settings)
    try:
        return await run_command(
            args,
            GraphCheckpointRunRepository(pool),
            GraphRunErrorRepository(pool),
        )
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
