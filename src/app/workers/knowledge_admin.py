import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import KnowledgeDocumentRepository


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lightweight knowledge document admin CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("list", "get", "enable", "disable"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--tenant-id", default="default")
        subparser.add_argument("--kb-scope", default="default")
        if command == "list":
            subparser.add_argument("--enabled", choices=("true", "false"))
            subparser.add_argument("--limit", type=int, default=50)
        else:
            subparser.add_argument("--title", required=True)
    return parser


async def run_command(args, repository) -> dict:
    if args.command == "list":
        enabled = None if args.enabled is None else args.enabled == "true"
        documents = await repository.list_documents(
            tenant_id=args.tenant_id,
            kb_scope=args.kb_scope,
            enabled=enabled,
            limit=args.limit,
        )
        return {"command": "list", "tenant_id": args.tenant_id, "kb_scope": args.kb_scope, "documents": documents}

    if args.command == "get":
        document = await repository.get_by_title(args.tenant_id, args.kb_scope, args.title)
        return {"command": "get", "tenant_id": args.tenant_id, "kb_scope": args.kb_scope, "document": document}

    enabled = args.command == "enable"
    result = await repository.set_enabled(args.tenant_id, args.kb_scope, args.title, enabled)
    return {
        "command": args.command,
        "tenant_id": args.tenant_id,
        "kb_scope": args.kb_scope,
        "title": args.title,
        "result": result,
    }


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    settings = Settings(
        livechat_agent_access_token="unused-for-seed",
        livechat_account_id="unused-for-seed",
    )
    pool = await create_pool(settings)
    try:
        repository = KnowledgeDocumentRepository(pool)
        return await run_command(args, repository)
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
