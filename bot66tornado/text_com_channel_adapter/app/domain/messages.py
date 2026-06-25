from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field


class ChannelType(StrEnum):
    TEXT_COM = "text_com"


class InboundEventType(StrEnum):
    CHAT_STARTED = "chat_started"
    MESSAGE_CREATED = "message_created"
    UNSUPPORTED = "unsupported"


class SenderRole(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    BOT = "bot"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class ChannelIdentity(BaseModel):
    tenant_id: str
    organization_id: str | None = None
    channel_type: ChannelType = ChannelType.TEXT_COM
    channel_instance_id: str = "text_com_default"


class CanonicalInboundEvent(BaseModel):
    """Normalized event passed from Channel Adapter into Gateway / AI orchestration."""

    event_id: str
    event_type: InboundEventType
    channel: ChannelIdentity

    chat_id: str
    thread_id: str | None = None
    customer_id: str | None = None
    sender_id: str | None = None
    sender_role: SenderRole = SenderRole.UNKNOWN

    text: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    raw_action: str
    raw_payload: dict[str, Any]

    @property
    def conversation_key(self) -> str:
        return f"{self.channel.tenant_id}:{self.channel.channel_type}:{self.chat_id}:{self.thread_id or 'no_thread'}"


class OutboundMessage(BaseModel):
    channel: ChannelIdentity
    chat_id: str
    text: str
    visibility: Literal["all", "agents"] = "all"
    thread_id: str | None = None
    correlation_id: str | None = None
