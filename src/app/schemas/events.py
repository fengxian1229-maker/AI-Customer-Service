from pydantic import BaseModel


class InboundEvent(BaseModel):
    source: str
    raw_action: str
    organization_id: str | None = None
    chat_id: str | None = None
    thread_id: str | None = None
    event_id: str | None = None
    event_type: str | None = None
    standard_event_type: str
    author_id: str | None = None
    sender_role: str
    occurred_at: str | None = None
    dedup_key: str
    payload_json: dict
    ignored: bool
    ignore_reason: str | None = None
