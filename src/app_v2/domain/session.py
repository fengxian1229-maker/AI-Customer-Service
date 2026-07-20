from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app_v2.domain.workflow import WorkflowInstance


class SessionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceStatus(StrEnum):
    BOT_ACTIVE = "BOT_ACTIVE"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    CLOSED = "CLOSED"


class ClarificationState(SessionContract):
    scope: str | None = Field(default=None, min_length=1)
    failure_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_scope_and_count(self) -> ClarificationState:
        if (self.scope is None) != (self.failure_count == 0):
            raise ValueError("clarification scope and failure_count must be set or cleared together")
        return self


class HandoffState(SessionContract):
    directive_id: str = Field(min_length=1)
    requested_at: datetime


class SessionState(SessionContract):
    schema_version: int = Field(ge=1)
    conversation_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    version: int = Field(ge=0)
    runtime_version: str = Field(min_length=1)
    service_status: ServiceStatus
    conversation_language: str | None = None
    clarification: ClarificationState = Field(default_factory=ClarificationState)
    active_workflow: WorkflowInstance | None = None
    handoff: HandoffState | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_handoff_pending_state(self) -> SessionState:
        if (self.service_status == ServiceStatus.HANDOFF_PENDING) != (self.handoff is not None):
            raise ValueError("handoff must exist if and only if service_status is HANDOFF_PENDING")
        return self
