import argparse
import asyncio
import json
import logging

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    GatewayTransactionRepository,
    GraphCheckpointRunRepository,
    InboundEventRepository,
    KnowledgeDocumentRepository,
)
from app.graph.checkpointing import build_checkpointer
from app.llm.final_reply_provider import FinalReplyLLMProvider
from app.llm.provider import build_llm_provider
from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService
from app.services.final_reply_service import FinalReplyService
from app.services.rag import RagService


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger("app.graph.nodes").setLevel(logging.INFO)


async def process_next_batch(pool, limit: int = 20, checkpoint_mode: str = "off", settings=None) -> dict:
    inbound_repository, service, managed_checkpointer = _build_gateway_dependencies(pool, checkpoint_mode, settings)
    try:
        rows = await inbound_repository.fetch_unprocessed(limit=limit)
        result = await _process_rows(service, rows)
        return {
            **result,
            "llm": _build_llm_summary(settings),
        }
    finally:
        managed_checkpointer.close()


async def process_inbound_event_id(pool, inbound_event_id: int, checkpoint_mode: str = "off", settings=None) -> dict:
    inbound_repository, service, managed_checkpointer = _build_gateway_dependencies(pool, checkpoint_mode, settings)
    try:
        row = await inbound_repository.fetch_unprocessed_by_id(inbound_event_id)
        if not row:
            return {
                "processed": 0,
                "failed": 0,
                "enqueued": 0,
                "not_found": True,
                "inbound_event_id": inbound_event_id,
                "failures": [],
                "results": [],
                "llm": _build_llm_summary(settings),
            }
        result = await _process_rows(service, [row])
        return {
            **result,
            "not_found": False,
            "inbound_event_id": inbound_event_id,
            "llm": _build_llm_summary(settings),
        }
    finally:
        managed_checkpointer.close()


def _build_gateway_dependencies(pool, checkpoint_mode: str, settings):
    inbound_repository = InboundEventRepository(pool)
    transactional_repository = GatewayTransactionRepository(pool, inbound_repository=inbound_repository)
    knowledge_repository = KnowledgeDocumentRepository(pool)
    checkpoint_run_repository = GraphCheckpointRunRepository(pool)
    rag_service = RagService(knowledge_repository=knowledge_repository)
    llm_provider = build_llm_provider(getattr(settings, "llm_provider", "off"), settings=settings) if settings else None
    final_reply_service = _build_final_reply_service(settings)
    managed_checkpointer = build_checkpointer(checkpoint_mode, settings=settings)
    service_kwargs = {
        "transactional_repository": transactional_repository,
        "checkpointer": managed_checkpointer.checkpointer,
        "checkpoint_mode": checkpoint_mode,
        "checkpoint_run_repository": checkpoint_run_repository,
        "rag_service": rag_service,
    }
    if settings is not None:
        service_kwargs.update(
            {
                "language_detection_enabled": getattr(settings, "language_detection_enabled", True),
                "language_detection_min_confidence": getattr(settings, "language_detection_min_confidence", 0.70),
                "tenant_persona_default_language": getattr(settings, "tenant_persona_default_language", "zh-Hans"),
                "tenant_supported_languages": getattr(settings, "tenant_supported_languages", "zh-Hans,zh-Hant,en,es,tl,th,my,ms"),
                "language_fallback": getattr(settings, "language_fallback", "zh-Hans"),
                "language_persist_to_slot_memory": getattr(settings, "language_persist_to_slot_memory", True),
            }
        )
    if any(
        [
            llm_provider is not None,
            getattr(settings, "llm_rewrite_shadow_enabled", False),
            getattr(settings, "llm_rewrite_fallback_enabled", False),
            getattr(settings, "llm_intent_shadow_enabled", False),
            getattr(settings, "llm_intent_fallback_enabled", False),
            getattr(settings, "llm_sop_slot_enabled", False),
            getattr(settings, "llm_final_reply_enabled", False),
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
                "llm_intent_fallback_to_deterministic": getattr(settings, "llm_intent_fallback_to_deterministic", True),
                "llm_sop_slot_service": llm_provider if getattr(settings, "llm_sop_slot_enabled", False) else None,
                "llm_sop_slot_enabled": getattr(settings, "llm_sop_slot_enabled", False),
                "llm_sop_slot_min_confidence": getattr(settings, "llm_sop_slot_min_confidence", 0.70),
                "llm_sop_slot_fallback_to_deterministic": getattr(settings, "llm_sop_slot_fallback_to_deterministic", True),
                "llm_final_reply_service": final_reply_service,
                "llm_final_reply_enabled": getattr(settings, "llm_final_reply_enabled", False),
                "llm_final_reply_min_confidence": getattr(settings, "llm_final_reply_min_confidence", 0.70),
                "llm_final_reply_fallback_enabled": getattr(settings, "llm_final_reply_fallback_enabled", True),
            }
        )
    service = GatewayService(**service_kwargs)
    return inbound_repository, service, managed_checkpointer


def _build_final_reply_service(settings):
    if not settings or not getattr(settings, "llm_final_reply_enabled", False):
        return None
    provider_name = str(getattr(settings, "llm_provider", "off") or "off").lower()
    if provider_name == "gemini":
        provider = FinalReplyLLMProvider(settings)
    elif provider_name == "mock":
        provider = build_llm_provider(provider_name, settings=settings)
    else:
        provider = None
    return FinalReplyService(
        provider=provider,
        enabled=getattr(settings, "llm_final_reply_enabled", False),
        min_confidence=getattr(settings, "llm_final_reply_min_confidence", 0.70),
        fallback_enabled=getattr(settings, "llm_final_reply_fallback_enabled", True),
        tenant_persona={
            "default_language": getattr(settings, "tenant_persona_default_language", "zh-Hans"),
            "supported_languages": getattr(settings, "tenant_supported_languages", "zh-Hans,zh-Hant,en,es,tl,th,my,ms"),
            "tone": getattr(settings, "tenant_persona_tone", "polite"),
            "assistant_name": getattr(settings, "tenant_persona_assistant_name", None),
            "brand_name": getattr(settings, "tenant_persona_brand_name", None),
        },
    )


async def _process_rows(service, rows: list[dict]) -> dict:
    results = []
    failures = []
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
    }


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
        "rewrite_fallback_enabled": bool(getattr(settings, "llm_rewrite_fallback_enabled", False)),
        "intent_fallback_enabled": bool(getattr(settings, "llm_intent_fallback_enabled", False)),
        "intent_mode": "guarded_authoritative",
        "intent_min_confidence": getattr(settings, "llm_intent_min_confidence", 0.75),
        "intent_fallback_to_deterministic": bool(getattr(settings, "llm_intent_fallback_to_deterministic", True)),
        "sop_slot_enabled": bool(getattr(settings, "llm_sop_slot_enabled", False)),
        "sop_slot_min_confidence": getattr(settings, "llm_sop_slot_min_confidence", 0.70),
    }
    if getattr(settings, "llm_final_reply_enabled", False):
        summary.update(
            {
                "final_reply_enabled": True,
                "final_reply_min_confidence": getattr(settings, "llm_final_reply_min_confidence", 0.70),
                "final_reply_fallback_enabled": bool(getattr(settings, "llm_final_reply_fallback_enabled", True)),
            }
        )
    summary["fallback_enabled"] = bool(summary["rewrite_fallback_enabled"] or summary["intent_fallback_enabled"])
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
    configure_logging()
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(run_once(limit=args.limit))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
