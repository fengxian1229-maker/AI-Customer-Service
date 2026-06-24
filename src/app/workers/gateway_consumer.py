from app.db.repositories import ConversationRepository, InboundEventRepository, OutboundMessageRepository
from app.schemas.events import InboundEvent
from app.services.gateway import GatewayService


async def process_next_batch(pool, limit: int = 20) -> list[dict]:
    inbound_repository = InboundEventRepository(pool)
    conversation_repository = ConversationRepository(pool)
    outbound_repository = OutboundMessageRepository(pool)
    service = GatewayService(inbound_repository, conversation_repository, outbound_repository)

    results = []
    rows = await inbound_repository.fetch_unprocessed(limit=limit)
    for row in rows:
        inbound_event_id = row.pop("id")
        event = InboundEvent(**row)
        results.append(await service.process_event(inbound_event_id, event))
    return results
