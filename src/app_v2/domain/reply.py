from pydantic import BaseModel, ConfigDict, Field


class ReplyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1)
    response_kind: str = Field(min_length=1)
    allowed_facts: dict[str, object]
    required_facts: list[str]
    prohibited_claims: list[str]
    related_event_ids: list[str]
