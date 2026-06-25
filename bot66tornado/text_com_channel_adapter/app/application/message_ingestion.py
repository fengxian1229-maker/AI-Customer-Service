import logging
from app.domain.messages import CanonicalInboundEvent, InboundEventType
from app.infrastructure.idempotency.memory import InMemoryIdempotencyStore

logger = logging.getLogger(__name__)


class MessageIngestionService:
    """Boundary between Channel Adapter and AI Gateway.

    Current responsibility:
    1. event deduplication
    2. filtering unsupported events
    3. handoff to orchestration placeholder

    Later replacement:
    - publish to message_outbox / Kafka / Redis Stream
    - load TenantConfig
    - build GraphState
    - call LangGraph workflow
    """

    def __init__(self, idempotency_store: InMemoryIdempotencyStore):
        self._idempotency_store = idempotency_store

    async def accept(self, events: list[CanonicalInboundEvent]) -> dict:
        accepted = 0
        duplicated = 0
        skipped = 0

        for event in events:
            dedup_key = f"{event.channel.channel_type}:{event.chat_id}:{event.thread_id}:{event.event_id}"
            if self._idempotency_store.seen_or_mark(dedup_key):
                duplicated += 1
                continue

            if event.event_type == InboundEventType.UNSUPPORTED:
                skipped += 1
                logger.info("Unsupported channel event skipped: %s", event.model_dump(mode="json"))
                continue

            await self._handoff_to_gateway(event)
            accepted += 1

        return {
            "accepted": accepted,
            "duplicated": duplicated,
            "skipped": skipped,
        }

    async def _handoff_to_gateway(self, event: CanonicalInboundEvent) -> None:
        # This is intentionally a seam. Do not put SOP / KB / Skill logic here.
        # Replace this method with an outbox insert or message bus publish.
        logger.info(
            "Accepted event conversation_key=%s event_type=%s text=%r",
            event.conversation_key,
            event.event_type,
            event.text,
        )
