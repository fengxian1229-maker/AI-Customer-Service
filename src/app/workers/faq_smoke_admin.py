import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import FaqSmokeReadRepository


COMMANDS = {
    "latest-inbound": "latest_inbound",
    "latest-outbound": "latest_outbound",
    "latest-conversation": "latest_conversation",
    "latest-checkpoints": "latest_checkpoints",
    "latest-errors": "latest_errors",
    "summary": "summary",
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only FAQ single-text smoke diagnostics.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--conversation-id", help="Filter by conversation_id.")
    parser.add_argument("--chat-id", help="Filter by LiveChat chat_id.")
    parser.add_argument("--inbound-event-id", type=int, help="Filter by inbound_events.id.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum rows to read.")
    return parser


async def run_command(
    command: str,
    conversation_id: str | None = None,
    chat_id: str | None = None,
    inbound_event_id: int | None = None,
    limit: int = 20,
) -> dict | list[dict]:
    settings = Settings(
        livechat_agent_access_token="unused-for-faq-smoke-admin",
        livechat_account_id="unused-for-faq-smoke-admin",
    )
    pool = await create_pool(settings)
    try:
        repository = FaqSmokeReadRepository(pool)
        method = getattr(repository, COMMANDS[command])
        return await method(
            conversation_id=conversation_id,
            chat_id=chat_id,
            inbound_event_id=inbound_event_id,
            limit=limit,
        )
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(
        run_command(
            args.command,
            conversation_id=args.conversation_id,
            chat_id=args.chat_id,
            inbound_event_id=args.inbound_event_id,
            limit=args.limit,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
