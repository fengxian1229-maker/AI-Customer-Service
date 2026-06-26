import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    GatewayTransactionRepository,
    GraphCheckpointRunRepository,
    InboundEventRepository,
    KnowledgeDocumentRepository,
)
from app.graph.checkpointing import build_checkpointer
from app.llm.provider import build_llm_provider
from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService
from app.services.rag import RagService


async def process_next_batch(pool, limit: int = 20, checkpoint_mode: str = "off", settings=None) -> dict:
    inbound_repository = InboundEventRepository(pool)
    transactional_repository = GatewayTransactionRepository(pool, inbound_repository=inbound_repository)
    knowledge_repository = KnowledgeDocumentRepository(pool)
    checkpoint_run_repository = GraphCheckpointRunRepository(pool)
    rag_service = RagService(knowledge_repository=knowledge_repository)
    llm_provider = build_llm_provider(getattr(settings, "llm_provider", "off"), settings=settings) if settings else None
    managed_checkpointer = build_checkpointer(checkpoint_mode, settings=settings)
    try:
        service_kwargs = {
            "transactional_repository": transactional_repository,
            "checkpointer": managed_checkpointer.checkpointer,
            "checkpoint_mode": checkpoint_mode,
            "checkpoint_run_repository": checkpoint_run_repository,
            "rag_service": rag_service,
        }
        if any(
            [
                llm_provider is not None,
                getattr(settings, "llm_rewrite_shadow_enabled", False),
                getattr(settings, "llm_rewrite_fallback_enabled", False),
                getattr(settings, "llm_intent_shadow_enabled", False),
                getattr(settings, "llm_intent_fallback_enabled", False),
            ]
        ):
            service_kwargs.update(
                {
                    "llm_rewrite_service": llm_provider,
                    "llm_intent_service": llm_provider,
                    "llm_rewrite_shadow_enabled": getattr(settings, "llm_rewrite_shadow_enabled", False),
                    "llm_rewrite_fallback_enabled": getattr(settings, "llm_rewrite_fallback_enabled", False),
                    "llm_intent_shadow_enabled": getattr(settings, "llm_intent_shadow_enabled", False),
                    "llm_intent_fallback_enabled": getattr(settings, "llm_intent_fallback_enabled", False),
                    "llm_intent_min_confidence": getattr(settings, "llm_intent_min_confidence", 0.75),
                }
            )
        service = GatewayService(
            **service_kwargs,
        )

        results = []
        failures = []
        rows = await inbound_repository.fetch_unprocessed(limit=limit)
        for row in rows:
            inbound_event_id = row.pop("id")
            event = InboundEvent(**row)
            try:
                results.append(await service.process_event(inbound_event_id, event))
            except Exception as exc:
                failures.append(
                    {
                        "inbound_event_id": inbound_event_id,
                        "event_id": event.event_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
        return {
            "results": results,
            "failures": failures,
            "processed": len(results),
            "failed": len(failures),
            "enqueued": sum(1 for result in results if result.get("outbound_message")),
            "llm": _build_llm_summary(settings),
        }
    finally:
        managed_checkpointer.close()


def _build_llm_summary(settings) -> dict:
    if not settings:
        return {
            "provider": "off",
            "rewrite_shadow_enabled": False,
            "intent_shadow_enabled": False,
            "shadow_active": False,
        }
    provider = (getattr(settings, "llm_provider", "off") or "off").lower()
    summary = {
        "provider": provider,
        "rewrite_shadow_enabled": bool(getattr(settings, "llm_rewrite_shadow_enabled", False)),
        "intent_shadow_enabled": bool(getattr(settings, "llm_intent_shadow_enabled", False)),
    }
    summary["shadow_active"] = bool(
        provider != "off"
        and (summary["rewrite_shadow_enabled"] or summary["intent_shadow_enabled"])
    )
    if provider == "gemini":
        summary.update(
            {
                "model": getattr(settings, "gemini_model", None),
                "vertexai": bool(getattr(settings, "gemini_vertexai", False)),
                "project": getattr(settings, "gemini_project", None),
                "location": getattr(settings, "gemini_location", None),
            }
        )
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume inbound_events into conversation state and outbox.")
    parser.add_argument("--once", action="store_true", help="Run one gateway batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum inbound events to process.")
    return parser


async def run_once(limit: int) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-gateway",
        livechat_account_id="unused-for-gateway",
    )
    pool = await create_pool(settings)
    try:
        results = await process_next_batch(
            pool,
            limit=limit,
            checkpoint_mode=settings.langgraph_checkpoint_mode,
            settings=settings,
        )
        return {
            "worker": "gateway_consumer",
            "mode": "once",
            "processed": results["processed"],
            "failed": results["failed"],
            "enqueued": results["enqueued"],
            "failures": results["failures"],
            "llm": results.get("llm", _build_llm_summary(settings)),
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
