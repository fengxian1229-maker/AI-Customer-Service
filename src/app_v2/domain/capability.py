from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_request_id: str = Field(min_length=1)
    workflow_instance_id: str = Field(min_length=1)
    capability_id: Literal["backend_query"]
    query_type: Literal["turnover_requirement"]
    validated_payload: dict[str, object]
