import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import GraphCheckpointRunRepository


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only LLM shadow diagnostics.")
    parser.add_argument("command", choices=["latest", "summary"])
    parser.add_argument("--conversation-id", help="Filter by conversation_id.")
    parser.add_argument("--chat-id", help="Filter by LiveChat chat_id.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum checkpoint rows to read.")
    return parser


async def run_command(
    command: str,
    conversation_id: str | None = None,
    chat_id: str | None = None,
    limit: int = 20,
) -> dict | list[dict]:
    settings = Settings(
        livechat_agent_access_token="unused-for-llm-shadow-admin",
        livechat_account_id="unused-for-llm-shadow-admin",
    )
    pool = await create_pool(settings)
    try:
        repository = GraphCheckpointRunRepository(pool)
        rows = await repository.list_runs(
            conversation_id=conversation_id or _conversation_id_from_chat_id(chat_id),
            limit=limit,
        )
        latest = [_shadow_entry_from_checkpoint(row) for row in rows if _shadow_entry_from_checkpoint(row)]
        if command == "latest":
            return latest
        return _build_summary(latest)
    finally:
        pool.close()
        await pool.wait_closed()


def _shadow_entry_from_checkpoint(row: dict) -> dict | None:
    metadata = row.get("metadata_json") or {}
    shadow = metadata.get("llm_shadow")
    if not shadow:
        return None
    return {
        "checkpoint_run_id": row.get("id"),
        "conversation_id": row.get("conversation_id"),
        "graph_thread_id": row.get("graph_thread_id"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "llm_shadow": _sanitize_shadow(shadow),
    }


def _build_summary(entries: list[dict]) -> dict:
    rewrite_results = [entry["llm_shadow"].get("rewrite") for entry in entries if entry["llm_shadow"].get("rewrite")]
    intent_results = [entry["llm_shadow"].get("intent") for entry in entries if entry["llm_shadow"].get("intent")]
    errors = [
        result
        for result in [*rewrite_results, *intent_results]
        if result.get("status") == "error"
    ]
    return {
        "total": len(entries),
        "rewrite_count": len(rewrite_results),
        "intent_count": len(intent_results),
        "error_count": len(errors),
        "latest": entries[0] if entries else None,
    }


def _sanitize_shadow(value):
    sensitive_tokens = ("token", "access_token", "secret", "api_key", "password")
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in sensitive_tokens):
                continue
            sanitized[key] = _sanitize_shadow(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_shadow(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:500]
    return value


def _conversation_id_from_chat_id(chat_id: str | None) -> str | None:
    if chat_id:
        return f"livechat:{chat_id}"
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(
        run_command(
            args.command,
            conversation_id=args.conversation_id,
            chat_id=args.chat_id,
            limit=args.limit,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
