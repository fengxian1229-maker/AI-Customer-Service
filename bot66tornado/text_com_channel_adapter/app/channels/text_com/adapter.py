from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from app.channels.base import ChannelAdapter
from app.channels.text_com.client import TextComAgentChatClient
from app.channels.text_com.models import TextComWebhookEnvelope
from app.core.config import Settings
from app.core.security import verify_text_com_payload_secret
from app.domain.messages import (
    CanonicalInboundEvent,
    ChannelIdentity,
    ChannelType,
    InboundEventType,
    OutboundMessage,
    SenderRole,
)

logger = logging.getLogger(__name__)


class TextComChannelAdapter(ChannelAdapter):
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = TextComAgentChatClient(settings)

    def parse_webhook(self, body: dict[str, Any]) -> list[CanonicalInboundEvent]:
        envelope = TextComWebhookEnvelope.model_validate(body)
        verify_text_com_payload_secret(
            received_secret=envelope.secret_key,
            expected_secret=self._settings.text_com_webhook_secret,
        )

        if envelope.action == "incoming_chat":
            return self._parse_incoming_chat(envelope)
        if envelope.action == "incoming_event":
            return self._parse_incoming_event(envelope)

        logger.info("Unsupported Text.com webhook action skipped: %s", envelope.action)
        return []

    async def send_text(self, message: OutboundMessage) -> str:
        return await self._client.send_event(
            chat_id=message.chat_id,
            text=message.text,
            visibility=message.visibility,
        )

    def _channel_identity(self, envelope: TextComWebhookEnvelope) -> ChannelIdentity:
        # Temporary resolver. Later replace with TenantConfigService:
        # organization_id -> tenant_id/channel_instance_id/channel credentials/SOP/KB.
        return ChannelIdentity(
            tenant_id=self._settings.default_tenant_id,
            organization_id=envelope.organization_id,
            channel_type=ChannelType.TEXT_COM,
            channel_instance_id=f"text_com:{envelope.organization_id or 'unknown'}",
        )

    def _parse_incoming_chat(self, envelope: TextComWebhookEnvelope) -> list[CanonicalInboundEvent]:
        chat = envelope.payload.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id:
            logger.warning("incoming_chat payload missing chat.id: %s", envelope.payload)
            return []

        thread = chat.get("thread") or {}
        thread_id = thread.get("id")
        customer_id = self._find_customer_id(chat)
        channel = self._channel_identity(envelope)

        normalized: list[CanonicalInboundEvent] = [
            CanonicalInboundEvent(
                event_id=f"{envelope.webhook_id or 'webhook'}:{chat_id}:{thread_id or 'no_thread'}:chat_started",
                event_type=InboundEventType.CHAT_STARTED,
                channel=channel,
                chat_id=chat_id,
                thread_id=thread_id,
                customer_id=customer_id,
                sender_role=SenderRole.SYSTEM,
                raw_action=envelope.action,
                raw_payload=envelope.model_dump(mode="json"),
            )
        ]

        # If incoming_chat includes initial thread events, normalize customer message events too.
        for event in thread.get("events") or []:
            canonical = self._event_to_canonical(
                envelope=envelope,
                event=event,
                chat_id=chat_id,
                thread_id=thread_id,
                customer_id=customer_id,
            )
            if canonical:
                normalized.append(canonical)

        return normalized

    def _parse_incoming_event(self, envelope: TextComWebhookEnvelope) -> list[CanonicalInboundEvent]:
        payload = envelope.payload
        event = payload.get("event") or {}
        chat_id = payload.get("chat_id")
        thread_id = payload.get("thread_id")
        if not chat_id or not event:
            logger.warning("incoming_event payload missing chat_id or event: %s", payload)
            return []

        canonical = self._event_to_canonical(
            envelope=envelope,
            event=event,
            chat_id=chat_id,
            thread_id=thread_id,
            customer_id=None,
        )
        return [canonical] if canonical else []

    def _event_to_canonical(
        self,
        envelope: TextComWebhookEnvelope,
        event: dict[str, Any],
        chat_id: str,
        thread_id: str | None,
        customer_id: str | None,
    ) -> CanonicalInboundEvent | None:
        event_type = event.get("type")
        event_id = event.get("id") or f"{envelope.webhook_id or 'webhook'}:{chat_id}:{thread_id}:{event_type}:unknown"
        author_id = event.get("author_id") or event.get("user_id")

        if author_id and author_id in self._settings.ignored_author_ids:
            logger.info("Skip message from ignored Text.com author_id=%s event_id=%s", author_id, event_id)
            return None

        if event_type != "message":
            return CanonicalInboundEvent(
                event_id=event_id,
                event_type=InboundEventType.UNSUPPORTED,
                channel=self._channel_identity(envelope),
                chat_id=chat_id,
                thread_id=thread_id,
                customer_id=customer_id,
                sender_id=author_id,
                sender_role=self._infer_sender_role(event, author_id),
                occurred_at=self._parse_datetime(event.get("created_at")),
                raw_action=envelope.action,
                raw_payload={"envelope": envelope.model_dump(mode="json"), "event": event},
            )

        text = event.get("text")
        if not text:
            logger.info("Skip Text.com message event without text: event_id=%s", event_id)
            return None

        return CanonicalInboundEvent(
            event_id=event_id,
            event_type=InboundEventType.MESSAGE_CREATED,
            channel=self._channel_identity(envelope),
            chat_id=chat_id,
            thread_id=thread_id,
            customer_id=customer_id,
            sender_id=author_id,
            sender_role=self._infer_sender_role(event, author_id),
            text=text,
            occurred_at=self._parse_datetime(event.get("created_at")),
            raw_action=envelope.action,
            raw_payload={"envelope": envelope.model_dump(mode="json"), "event": event},
        )

    @staticmethod
    def _find_customer_id(chat: dict[str, Any]) -> str | None:
        for user in chat.get("users") or []:
            if user.get("type") == "customer":
                return user.get("id")
        return None

    @staticmethod
    def _infer_sender_role(event: dict[str, Any], author_id: str | None) -> SenderRole:
        # Text.com event payload variants can differ by API version and event type.
        # Keep this tolerant and refine when real payload samples are captured.
        author_type = event.get("author_type") or event.get("user_type") or event.get("source")
        if author_type in {"customer", "agent", "bot", "system"}:
            return SenderRole(author_type)
        if author_id and "@" in author_id:
            return SenderRole.AGENT
        return SenderRole.UNKNOWN

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
