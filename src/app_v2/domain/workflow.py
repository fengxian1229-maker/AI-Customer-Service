from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotValue(WorkflowContract):
    value: object
    source_message_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)


class WorkflowInstance(WorkflowContract):
    workflow_instance_id: str = Field(min_length=1)
    workflow_name: Literal["turnover_requirement_query"]
    slots: dict[str, SlotValue]
    pending_query_request_id: str | None = None
    started_at: datetime
    updated_at: datetime
