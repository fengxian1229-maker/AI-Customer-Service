import argparse
import asyncio
import inspect
import json
import logging
import socket

from app.channels.livechat.sender_client import LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    GatewayTransactionRepository,
    GraphCheckpointRunRepository,
    InboundEventRepository,
    KnowledgeDocumentRepository,
)
from app.graph.checkpointing import build_async_checkpointer as build_checkpointer
from app.llm.provider import build_llm_provider
from app.schemas.events import InboundEvent
from app.services.final_reply_factory import build_final_reply_service_from_settings
from app.services.final_reply_streaming_service import FinalReplyStreamingService
from app.services.gateway import GatewayService
from app.services.image_analysis import ImageAttachmentAnalyzer
from app.services.rag import RagService


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger("app.graph.nodes").setLevel(logging.INFO)


def default_worker_id() -> str:
    return f"gateway-consumer-{socket.gethostname()}"


async def process_next_batch(
    pool,
    limit: int = 20,
    checkpoint_mode: str = "off",
    settings=None,
    concurrency: int | None = None,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
) -> dict:
    inbound_repository, service, managed_checkpointer = await _build_gateway_dependencies(pool, checkpoint_mode, settings)
    try:
        worker_id = worker_id or default_worker_id()
        lease_seconds = lease_seconds or getattr(settings, "worker_lease_seconds", 300)
        concurrency = max(int(concurrency if concurrency is not None else getattr(settings, "gateway_concurrency", 15)), 1)
        if hasattr(inbound_repository, "lease_unprocessed"):
            rows = await inbound_repository.lease_unprocessed(limit=limit, worker_id=worker_id, lease_seconds=lease_seconds)
        else:
            rows = await inbound_repository.fetch_unprocessed(limit=limit)
        result = await _process_rows(service, rows, concurrency=concurrency, inbound_repository=inbound_repository)
        return {
            **result,
            "llm": _build_llm_summary(settings),
            "concurrency": concurrency,
        }
    finally:
        await _close_managed_checkpointer(managed_checkpointer)


async def process_inbound_event_id(pool, inbound_event_id: int, checkpoint_mode: str = "off", settings=None) -> dict:
    inbound_repository, service, managed_checkpointer = await _build_gateway_dependencies(pool, checkpoint_mode, settings)
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
        await _close_managed_checkpointer(managed_checkpointer)


async def _build_gateway_dependencies(pool, checkpoint_mode: str, settings):
    inbound_repository = InboundEventRepository(pool)
    transactional_repository = GatewayTransactionRepository(pool, inbound_repository=inbound_repository)
    knowledge_repository = KnowledgeDocumentRepository(pool)
    checkpoint_run_repository = GraphCheckpointRunRepository(pool)
    rag_service = RagService(knowledge_repository=knowledge_repository)
    llm_provider = build_llm_provider(getattr(settings, "llm_provider", "off"), settings=settings) if settings else None
    final_reply_service = _build_final_reply_service(settings)
    final_reply_streaming_service = _build_final_reply_streaming_service(settings)
    livechat_sender_client = _build_livechat_sender_client(settings) if final_reply_streaming_service else None
    image_attachment_analyzer = ImageAttachmentAnalyzer(llm_provider) if llm_provider is not None else None
    managed_checkpointer = build_checkpointer(checkpoint_mode, settings=settings)
    if inspect.isawaitable(managed_checkpointer):
        managed_checkpointer = await managed_checkpointer
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
                "livechat_sender_client": livechat_sender_client,
                "final_reply_streaming_service": final_reply_streaming_service,
                "image_attachment_analyzer": image_attachment_analyzer,
                "llm_final_reply_streaming_enabled": getattr(settings, "llm_final_reply_streaming_enabled", False),
                "llm_final_reply_preview_enabled": getattr(settings, "llm_final_reply_preview_enabled", False),
                "llm_final_reply_preview_min_chars": getattr(settings, "llm_final_reply_preview_min_chars", 80),
                "llm_final_reply_preview_interval_ms": getattr(settings, "llm_final_reply_preview_interval_ms", 700),
                "llm_final_reply_preview_min_delta_chars": getattr(settings, "llm_final_reply_preview_min_delta_chars", 24),
                "llm_final_reply_preview_max_updates": getattr(settings, "llm_final_reply_preview_max_updates", 12),
                "livechat_typing_indicator_enabled": getattr(settings, "livechat_typing_indicator_enabled", True),
                "livechat_thinking_indicator_enabled": getattr(settings, "livechat_thinking_indicator_enabled", False),
            }
        )
    service = GatewayService(**service_kwargs)
    return inbound_repository, service, managed_checkpointer


async def _close_managed_checkpointer(managed_checkpointer) -> None:
    if hasattr(managed_checkpointer, "aclose"):
        await managed_checkpointer.aclose()
        return
    managed_checkpointer.close()


def _build_final_reply_service(settings):
    return build_final_reply_service_from_settings(settings)


def _build_final_reply_streaming_service(settings):
    if not settings or not (
        getattr(settings, "llm_final_reply_streaming_enabled", False)
        or getattr(settings, "llm_final_reply_preview_enabled", False)
    ):
        return None
    if str(getattr(settings, "llm_provider", "off") or "off").lower() != "gemini":
        return None
    return FinalReplyStreamingService(settings)


def _build_livechat_sender_client(settings):
    if not settings:
        return None
    return LiveChatSenderClient(
        settings.livechat_api_base,
        settings.livechat_account_id,
        settings.livechat_agent_access_token,
        agent_email=getattr(settings, "livechat_agent_email", None),
    )


async def _process_rows(service, rows: list[dict], concurrency: int = 1, inbound_repository=None) -> dict:
    results = []
    failures = []
    rows_by_conversation: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get("chat_id") or row.get("conversation_id") or f"inbound:{row.get('id')}")
        rows_by_conversation.setdefault(key, []).append(row)
    for group in rows_by_conversation.values():
        group.sort(key=lambda item: item.get("id") or 0)

    semaphore = asyncio.Semaphore(max(int(concurrency), 1))

    async def process_group(group: list[dict]) -> tuple[list[dict], list[dict]]:
        group_results = []
        group_failures = []
        async with semaphore:
            for row in group:
                result, failure = await _process_one_row(service, row, inbound_repository=inbound_repository)
                if failure:
                    group_failures.append(failure)
                else:
                    group_results.append(result)
        return group_results, group_failures

    group_outputs = await asyncio.gather(*(process_group(group) for group in rows_by_conversation.values()))
    for group_results, group_failures in group_outputs:
        results.extend(group_results)
        failures.extend(group_failures)
    return {
        "results": results,
        "failures": failures,
        "processed": len(results),
        "failed": len(failures),
        "enqueued": sum(1 for result in results if result.get("outbound_message")),
    }


async def _process_one_row(service, row: dict, inbound_repository=None) -> tuple[dict | None, dict | None]:
    row = dict(row)
    inbound_event_id = row.pop("id")
    event = InboundEvent(**row)
    try:
        return await service.process_event(inbound_event_id, event), None
    except Exception as exc:
        if inbound_repository is not None and hasattr(inbound_repository, "release_lease"):
            await inbound_repository.release_lease(inbound_event_id)
        return None, {
            "inbound_event_id": inbound_event_id,
            "event_id": event.event_id,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
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
    parser.add_argument("--concurrency", type=int, help="Maximum conversations to process concurrently.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id used for queue leases.")
    parser.add_argument("--lease-seconds", type=int, help="Seconds before an inbound lease expires.")
    return parser


async def run_once(
    limit: int,
    concurrency: int | None = None,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-gateway",
        livechat_account_id="unused-for-gateway",
    )
    pool = await create_pool(settings)
    try:
        process_kwargs = {
            "limit": limit,
            "checkpoint_mode": settings.langgraph_checkpoint_mode,
            "settings": settings,
        }
        if concurrency is not None:
            process_kwargs["concurrency"] = concurrency
        if worker_id is not None:
            process_kwargs["worker_id"] = worker_id
        if lease_seconds is not None:
            process_kwargs["lease_seconds"] = lease_seconds
        results = await process_next_batch(
            pool,
            **process_kwargs,
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
    result = asyncio.run(
        run_once(
            limit=args.limit,
            concurrency=args.concurrency,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
