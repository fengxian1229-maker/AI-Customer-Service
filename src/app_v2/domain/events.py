from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app_v2.domain.directives import ControlDirective


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentPart(ContractModel):
    content_type: Literal["text", "image", "video", "audio"]
    content: str = Field(min_length=1, strict=True)


SenderRole = Literal["customer", "ai_assistant", "human_agent", "system"]


class ConversationMessage(ContractModel):
    message_id: str = Field(min_length=1)
    sender_role: SenderRole
    content_part: ContentPart
    sent_at: datetime


class UserMessagePayload(ContractModel):
    message: ConversationMessage
    history_snapshot: list[ConversationMessage]

    @model_validator(mode="after")
    def validate_history_snapshot(self) -> UserMessagePayload:
        message_ids = [message.message_id for message in self.history_snapshot]
        if self.message.message_id in message_ids:
            raise ValueError("history_snapshot must not contain the current message")
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("history_snapshot message_id values must be unique")
        sent_times = [message.sent_at for message in self.history_snapshot]
        if sent_times != sorted(sent_times):
            raise ValueError("history_snapshot must be ordered by sent_at ascending")
        if sent_times and sent_times[-1] > self.message.sent_at:
            raise ValueError("history_snapshot must only contain messages before the current message")
        return self


ControlOutcome = Literal[
    "SUCCEEDED",
    "ALREADY_SUCCEEDED",
    "REJECTED_STALE",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
    "UNKNOWN_AFTER_EXTERNAL_SUCCESS",
]


class ControlDirectiveResultPayload(ContractModel):
    directive_id: str = Field(min_length=1)
    outcome: ControlOutcome
    completed_stages: list[str]
    error_code: str | None = None


class ConversationStatusUpdatePayload(ContractModel):
    previous_status: str = Field(min_length=1)
    new_status: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    effective_at: datetime


class AssistantMessagePayload(ContractModel):
    text: str = Field(min_length=1)
    reply_language: str = Field(min_length=1)
    response_kind: str = Field(min_length=1)
    in_reply_to_event_ids: list[str]


class ServiceErrorPayload(ContractModel):
    error_code: str = Field(min_length=1)
    retryable: bool
    related_event_id: str | None = None
    safe_message: str = Field(min_length=1)


class CapabilityResultEventPayload(ContractModel):
    query_request_id: str = Field(min_length=1)
    workflow_instance_id: str = Field(min_length=1)
    outcome: Literal["SUCCEEDED", "FAILED", "EXPIRED", "CANCELED"]
    result: dict[str, object] | None = None
    error_code: str | None = None


class CapabilityPendingDueEventPayload(ContractModel):
    query_request_id: str = Field(min_length=1)
    workflow_instance_id: str = Field(min_length=1)


class ResumeDeferredMessagesEventPayload(ContractModel):
    handoff_directive_id: str = Field(min_length=1)
    deferred_event_ids: list[str] = Field(min_length=1)


class BaseEventEnvelope(ContractModel):
    event_id: str = Field(min_length=1)
    event_type: str
    tenant_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    conversation_sequence: int = Field(ge=1)
    occurred_at: datetime


class UserMessageEvent(BaseEventEnvelope):
    event_type: Literal["UserMessage"]
    payload: UserMessagePayload

    @model_validator(mode="after")
    def validate_current_message(self) -> UserMessageEvent:
        if self.payload.message.sender_role != "customer":
            raise ValueError("UserMessage payload.message.sender_role must be customer")
        if self.occurred_at != self.payload.message.sent_at:
            raise ValueError("UserMessage occurred_at must equal message.sent_at")
        return self


class ControlDirectiveResultEvent(BaseEventEnvelope):
    event_type: Literal["ControlDirectiveResult"]
    payload: ControlDirectiveResultPayload


class ConversationStatusUpdateEvent(BaseEventEnvelope):
    event_type: Literal["ConversationStatusUpdate"]
    payload: ConversationStatusUpdatePayload


class AssistantMessageEvent(BaseEventEnvelope):
    event_type: Literal["AssistantMessage"]
    payload: AssistantMessagePayload


class ControlDirectiveEvent(BaseEventEnvelope):
    event_type: Literal["ControlDirective"]
    payload: ControlDirective


class ServiceErrorEvent(BaseEventEnvelope):
    event_type: Literal["ServiceError"]
    payload: ServiceErrorPayload


class CapabilityResultEvent(BaseEventEnvelope):
    event_type: Literal["CapabilityResultEvent"]
    payload: CapabilityResultEventPayload


class CapabilityPendingDueEvent(BaseEventEnvelope):
    event_type: Literal["CapabilityPendingDueEvent"]
    payload: CapabilityPendingDueEventPayload


class ResumeDeferredMessagesEvent(BaseEventEnvelope):
    event_type: Literal["ResumeDeferredMessagesEvent"]
    payload: ResumeDeferredMessagesEventPayload


EventEnvelope = Annotated[
    UserMessageEvent
    | ControlDirectiveResultEvent
    | ConversationStatusUpdateEvent
    | AssistantMessageEvent
    | ControlDirectiveEvent
    | ServiceErrorEvent
    | CapabilityResultEvent
    | CapabilityPendingDueEvent
    | ResumeDeferredMessagesEvent,
    Field(discriminator="event_type"),
]
_EVENT_ADAPTER = TypeAdapter(EventEnvelope)


def validate_event_envelope(value: object) -> EventEnvelope:
    """Validate untrusted event data into a typed V2 event envelope."""

    return _EVENT_ADAPTER.validate_python(value)
