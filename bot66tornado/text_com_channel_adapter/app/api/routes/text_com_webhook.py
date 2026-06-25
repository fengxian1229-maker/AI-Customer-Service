import logging
from typing import Any
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.application.message_ingestion import MessageIngestionService
from app.channels.text_com.adapter import TextComChannelAdapter
from app.core.config import Settings, get_settings
from app.infrastructure.idempotency.memory import InMemoryIdempotencyStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhooks/text-com", tags=["Text.com Webhooks"])

_idempotency_store = InMemoryIdempotencyStore()


def get_text_com_adapter(settings: Settings = Depends(get_settings)) -> TextComChannelAdapter:
    return TextComChannelAdapter(settings)


def get_ingestion_service() -> MessageIngestionService:
    return MessageIngestionService(_idempotency_store)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def receive_text_com_webhook(
    request: Request,
    adapter: TextComChannelAdapter = Depends(get_text_com_adapter),
    ingestion: MessageIngestionService = Depends(get_ingestion_service),
) -> JSONResponse:
    """Receive Text.com / LiveChat Chat Webhooks.

    Text.com can register incoming_chat and incoming_event as separate triggers.
    They can point to the same URL because the payload contains `action`.
    """
    body: dict[str, Any] = await request.json()
    events = adapter.parse_webhook(body)
    result = await ingestion.accept(events)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "ok": True,
            "action": body.get("action"),
            "normalized_events": len(events),
            **result,
        },
    )


@router.post("/incoming-chat", status_code=status.HTTP_202_ACCEPTED)
async def receive_incoming_chat_alias(
    request: Request,
    adapter: TextComChannelAdapter = Depends(get_text_com_adapter),
    ingestion: MessageIngestionService = Depends(get_ingestion_service),
) -> JSONResponse:
    """Optional alias if you prefer one URL per webhook trigger."""
    body: dict[str, Any] = await request.json()
    body.setdefault("action", "incoming_chat")
    events = adapter.parse_webhook(body)
    result = await ingestion.accept(events)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"ok": True, **result})


@router.post("/incoming-event", status_code=status.HTTP_202_ACCEPTED)
async def receive_incoming_event_alias(
    request: Request,
    adapter: TextComChannelAdapter = Depends(get_text_com_adapter),
    ingestion: MessageIngestionService = Depends(get_ingestion_service),
) -> JSONResponse:
    """Optional alias if you prefer one URL per webhook trigger."""
    body: dict[str, Any] = await request.json()
    body.setdefault("action", "incoming_event")
    events = adapter.parse_webhook(body)
    result = await ingestion.accept(events)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"ok": True, **result})
