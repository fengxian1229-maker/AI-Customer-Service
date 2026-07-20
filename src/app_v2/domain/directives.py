from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_id: str = Field(min_length=1)
    directive_type: Literal["handoff.requested"]
    customer_notice: str = Field(min_length=1)
