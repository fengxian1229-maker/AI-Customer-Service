import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ConversationRepository,
    ExternalCommandResultRepository,
    OutboundMessageRepository,
)
from app.services.outbox import build_text_outbox


RESULT_HANDLERS = {
    "telegram.send_case_card.mock_result": {
        "text": "资料已收到，我们会继续确认，请稍候。",
        "workflow_stage": "waiting_backend",
    },
    "telegram.append_to_case.mock_result": {
        "text": "补充资料已收到，我们会继续跟进，请稍候。",
        "workflow_stage": "waiting_backend",
    },
    "backend.query.mock_result": {
        "text": "已收到查询请求，当前为 dry-run 模式，未连接真实后台。",
        "workflow_stage": "backend_query_dry_run",
    },
    "pending_reply.lookup.mock_result": {
        "text": "已收到查询请求，当前为 dry-run 模式，未连接真实 pending reply 查询源。",
        "workflow_stage": "pending_reply_lookup_dry_run",
    },
    "human_handoff.requested.mock_result": {
        "text": "已为您转接真人客服，请稍候。",
        "active_workflow": "human_handoff",
        "workflow_stage": "handoff_requested",
        "status": "HANDOFF_REQUESTED",
    },
    "rag.placeholder.mock_result": {
        "text": "当前为 RAG placeholder，尚未接入真实知识库。",
        "workflow_stage": "rag_placeholder_dry_run",
    },
}


async def process_pending_results(
    result_repository: ExternalCommandResultRepository,
    conversation_repository: ConversationRepository,
    outbound_repository: OutboundMessageRepository,
    limit: int = 20,
) -> list[dict]:
    rows = await result_repository.fetch_pending(limit=limit)
    processed = []
    for row in rows:
        try:
            handler = RESULT_HANDLERS.get(row["result_type"])
            if handler is None:
                raise ValueError(f"unsupported result_type: {row['result_type']}")

            graph_state = {
                "status": handler.get("status") or "WAITING_EXTERNAL",
                "active_workflow": handler.get("active_workflow"),
                "workflow_stage": handler.get("workflow_stage"),
                "slot_memory": {},
            }
            await conversation_repository.update_workflow_state(row["conversation_id"], graph_state)
            outbound = build_text_outbox(
                chat_id=row["chat_id"],
                thread_id=row.get("thread_id"),
                conversation_id=row["conversation_id"],
                inbound_event_id=row.get("inbound_event_id"),
                text=handler["text"],
            )
            await outbound_repository.insert_idempotent(outbound)
            await result_repository.mark_processed(row["id"])
            processed.append({"id": row["id"], "result_type": row["result_type"], "status": "PROCESSED"})
        except Exception as exc:
            await result_repository.mark_failed(row["id"], str(exc))
            processed.append({"id": row["id"], "result_type": row.get("result_type"), "status": "FAILED", "error": str(exc)})
    return processed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume external_command_results into conversation state and outbox.")
    parser.add_argument("--once", action="store_true", help="Run one external result batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external command results to process.")
    return parser


async def run_once(limit: int) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-result-consumer",
        livechat_account_id="unused-for-external-result-consumer",
    )
    pool = await create_pool(settings)
    try:
        results = await process_pending_results(
            result_repository=ExternalCommandResultRepository(pool),
            conversation_repository=ConversationRepository(pool),
            outbound_repository=OutboundMessageRepository(pool),
            limit=limit,
        )
        return {
            "worker": "external_result_consumer",
            "mode": "once",
            "processed": len(results),
            "succeeded": sum(1 for result in results if result["status"] == "PROCESSED"),
            "failed": sum(1 for result in results if result["status"] == "FAILED"),
        }
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_once(limit=args.limit))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
