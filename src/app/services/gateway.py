from app.schemas.events import InboundEvent
from app.services.conversations import conversation_id_for_chat
from app.services.outbox import build_text_outbox


def should_enqueue_reply(event: InboundEvent) -> bool:
    return event.standard_event_type == "MESSAGE_CREATED" and not event.ignored


def build_fixed_reply(event: InboundEvent) -> dict:
    conversation_id = conversation_id_for_chat(event.chat_id or "unknown")
    return build_text_outbox(
        chat_id=event.chat_id,
        thread_id=event.thread_id,
        conversation_id=conversation_id,
    )


class GatewayService:
    def __init__(self, inbound_repository, conversation_repository, outbound_repository) -> None:
        self.inbound_repository = inbound_repository
        self.conversation_repository = conversation_repository
        self.outbound_repository = outbound_repository

    async def process_event(self, inbound_event_id: int, event: InboundEvent) -> dict:
        conversation = await self.conversation_repository.get_or_create(
            chat_id=event.chat_id or "unknown",
            thread_id=event.thread_id,
        )

        outbound_message = None
        should_reply = should_enqueue_reply(event)
        if should_reply:
            outbound_message = build_text_outbox(
                chat_id=event.chat_id,
                thread_id=event.thread_id,
                conversation_id=conversation["conversation_id"],
                inbound_event_id=inbound_event_id,
            )
            await self.outbound_repository.insert(outbound_message)

        await self.inbound_repository.mark_processed(inbound_event_id)

        return {
            "conversation": conversation,
            "should_reply": should_reply,
            "outbound_message": outbound_message,
        }
