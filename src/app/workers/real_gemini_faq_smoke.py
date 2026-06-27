import argparse
import asyncio
import json
import uuid
from pathlib import Path

import aiomysql

from app.channels.livechat.sender_client import LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import InboundEventRepository, KnowledgeDocumentRepository, OutboundMessageRepository
from app.schemas.events import InboundEvent
from app.workers import gateway_consumer, sender_worker
from app.workers.seed_knowledge import seed_repository


DEFAULT_SKIP_ERROR = "manual smoke uses fake chat_id; not sent"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real Gemini FAQ-authoritative smoke without sending by default.")
    parser.add_argument("--text", default="怎么存款？")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--kb-scope", default="default")
    parser.add_argument("--chat-id")
    parser.add_argument("--thread-id")
    parser.add_argument("--seed-default-faq", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--sender-limit", type=int, default=10)
    parser.add_argument("--mark-unsent-smoke-skipped", dest="mark_unsent_smoke_skipped", action="store_true", default=True)
    parser.add_argument("--no-mark-unsent-smoke-skipped", dest="mark_unsent_smoke_skipped", action="store_false")
    return parser


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    base_summary = _base_summary(args)

    if args.send and not args.chat_id:
        return {
            **base_summary,
            "error": {
                "code": "send_requires_explicit_chat_id",
                "message": "--send requires an explicit --chat-id to avoid sending to a generated fake chat.",
            },
            "smoke_success": False,
        }
    if args.send and not args.thread_id:
        return {
            **base_summary,
            "error": {
                "code": "send_requires_explicit_thread_id",
                "message": "--send requires an explicit --thread-id to avoid sending to a generated fake thread.",
            },
            "smoke_success": False,
        }

    try:
        settings = _build_settings(send=args.send)
    except Exception as exc:
        return {
            **base_summary,
            "error": {
                "code": "invalid_settings",
                "message": str(exc),
                "error_type": type(exc).__name__,
            },
            "smoke_success": False,
        }

    llm_provider = str(getattr(settings, "llm_provider", "") or "").strip().lower()
    llm_router_mode = str(getattr(settings, "llm_router_mode", "") or "").strip().lower()
    if llm_provider != "gemini" or llm_router_mode != "faq_authoritative":
        return {
            **base_summary,
            "error": {
                "code": "invalid_settings",
                "message": "real_gemini_faq_smoke requires llm_provider=gemini and llm_router_mode=faq_authoritative",
            },
            "smoke_success": False,
        }

    pool = await create_pool(settings)
    try:
        seed_result = None
        if args.seed_default_faq:
            seed_result = await seed_repository(
                KnowledgeDocumentRepository(pool),
                tenant_id=args.tenant_id,
                kb_scope=args.kb_scope,
                source_file=str(Path("data/knowledge/default_multimodal_faq_seed.json")),
            )
        inbound_event_id = await _insert_smoke_event(pool, args, base_summary)
        gateway_result = await gateway_consumer.process_inbound_event_id(
            pool,
            inbound_event_id=inbound_event_id,
            checkpoint_mode=settings.langgraph_checkpoint_mode,
            settings=settings,
        )
        conversation_id = base_summary["conversation_id"]
        llm_router = await _fetch_latest_router_metadata(pool, conversation_id)
        outbound_messages = await _fetch_outbound_messages(pool, conversation_id, inbound_event_id)
        graph_run_errors = await _fetch_graph_run_errors(pool, conversation_id)
        sender_results = []
        skipped_unsent_count = 0
        pending_before_count = sum(1 for message in outbound_messages if message.get("status") == "PENDING")
        if args.send:
            sender_client = LiveChatSenderClient(
                settings.livechat_api_base,
                settings.livechat_account_id,
                settings.livechat_agent_access_token,
            )
            sender_results = await sender_worker.process_pending_for_inbound_event(
                pool,
                sender_client,
                inbound_event_id=inbound_event_id,
                limit=args.sender_limit,
            )
            outbound_messages = await _fetch_outbound_messages(pool, conversation_id, inbound_event_id)
        elif args.mark_unsent_smoke_skipped:
            skipped_unsent_count = await OutboundMessageRepository(pool).mark_pending_by_inbound_event_skipped(
                inbound_event_id,
                error=DEFAULT_SKIP_ERROR,
            )
            outbound_messages = await _fetch_outbound_messages(pool, conversation_id, inbound_event_id)

        sender_ok = True
        warning = None if args.send else DEFAULT_SKIP_ERROR
        if args.send:
            sender_statuses = {result.get("status") for result in sender_results}
            sender_ok = bool(sender_results) and sender_statuses <= {"SENT", "SKIPPED_PREVIEW"}
            if "SKIPPED_PREVIEW" in sender_statuses:
                warning = "buttons preview was skipped by sender_worker"
        else:
            sender_ok = skipped_unsent_count == pending_before_count if args.mark_unsent_smoke_skipped else True

        smoke_success = (
            gateway_result.get("inbound_event_id") == inbound_event_id
            and gateway_result.get("processed") == 1
            and gateway_result.get("failed") == 0
            and (llm_router or {}).get("status") == "accepted"
            and (llm_router or {}).get("final_route") == "faq"
            and bool(outbound_messages)
            and not graph_run_errors
            and sender_ok
        )
        return {
            **base_summary,
            "inbound_event_id": inbound_event_id,
            "seed_result": seed_result,
            "gateway_result": gateway_result,
            "llm_router": llm_router,
            "outbound_messages": outbound_messages,
            "graph_run_errors": graph_run_errors,
            "sender_results": sender_results,
            "skipped_unsent_count": skipped_unsent_count,
            "warning": warning,
            "smoke_success": smoke_success,
        }
    finally:
        pool.close()
        await pool.wait_closed()


def _build_settings(send: bool):
    if send:
        return Settings()
    kwargs = {
        "livechat_agent_access_token": "unused-for-real-gemini-faq-smoke",
        "livechat_account_id": "unused-for-real-gemini-faq-smoke",
    }
    try:
        return Settings(**kwargs)
    except TypeError:
        return Settings()


def _base_summary(args) -> dict:
    smoke_id = uuid.uuid4().hex[:12]
    chat_id = args.chat_id or f"manual-gemini-faq-{smoke_id}:chat"
    thread_id = args.thread_id or f"manual-gemini-faq-{smoke_id}:thread"
    return {
        "worker": "real_gemini_faq_smoke",
        "input_text": args.text,
        "tenant_id": args.tenant_id,
        "kb_scope": args.kb_scope,
        "conversation_id": f"livechat:{chat_id}",
        "chat_id": chat_id,
        "thread_id": thread_id,
        "inbound_event_id": None,
        "seed_result": None,
        "gateway_result": None,
        "llm_router": None,
        "outbound_messages": [],
        "graph_run_errors": [],
        "sender_results": [],
        "skipped_unsent_count": 0,
        "warning": None,
    }


async def _insert_smoke_event(pool, args, summary: dict) -> int:
    event_id = f"manual-gemini-faq:{uuid.uuid4().hex}"
    event = InboundEvent(
        source="manual_smoke",
        raw_action="manual.real_gemini_faq_smoke.message",
        chat_id=summary["chat_id"],
        thread_id=summary["thread_id"],
        event_id=event_id,
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="manual-smoke",
        sender_role="external",
        occurred_at="2026-06-27 00:00:00.000000",
        dedup_key=event_id,
        payload_json={"event": {"type": "message", "text": args.text}, "text": args.text},
        ignored=False,
    )
    result = await InboundEventRepository(pool).insert(event)
    if result.get("id"):
        return result["id"]
    row = await _fetch_one(pool, "SELECT id FROM inbound_events WHERE dedup_key = %s", (event.dedup_key,))
    return row["id"]


async def _fetch_latest_router_metadata(pool, conversation_id: str) -> dict | None:
    row = await _fetch_one(
        pool,
        """
        SELECT metadata_json
        FROM graph_checkpoint_runs
        WHERE conversation_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (conversation_id,),
        required=False,
    )
    if not row:
        return None
    return (row.get("metadata_json") or {}).get("llm_router")


async def _fetch_outbound_messages(pool, conversation_id: str, inbound_event_id: int) -> list[dict]:
    return await _fetch_all(
        pool,
        """
        SELECT id, inbound_event_id, conversation_id, action_type, command_type,
               message_type, message_kind, block_index, status, last_error, payload_json
        FROM outbound_messages
        WHERE conversation_id = %s AND inbound_event_id = %s
        ORDER BY COALESCE(block_index, 0), id
        """,
        (conversation_id, inbound_event_id),
    )


async def _fetch_graph_run_errors(pool, conversation_id: str) -> list[dict]:
    return await _fetch_all(
        pool,
        "SELECT id, error_type, error_message FROM graph_run_errors WHERE conversation_id = %s ORDER BY id",
        (conversation_id,),
    )


async def _fetch_one(pool, sql: str, args: tuple, required: bool = True) -> dict | None:
    rows = await _fetch_all(pool, sql, args)
    if not rows:
        if required:
            raise RuntimeError("expected one row")
        return None
    return rows[0]


async def _fetch_all(pool, sql: str, args: tuple) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            rows = list(await cur.fetchall())
    for row in rows:
        for key in ("payload_json", "metadata_json"):
            if key in row and isinstance(row[key], str):
                row[key] = json.loads(row[key])
    return rows


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
