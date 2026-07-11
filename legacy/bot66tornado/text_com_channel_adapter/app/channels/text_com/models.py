from typing import Any, Literal
from pydantic import BaseModel, Field


class TextComWebhookEnvelope(BaseModel):
    webhook_id: str | None = None
    secret_key: str | None = None
    action: Literal["incoming_chat", "incoming_event"] | str
    organization_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    additional_data: dict[str, Any] | None = None
