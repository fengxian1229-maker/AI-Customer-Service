from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class RuntimeProfile(BaseModel):
    """Immutable, versioned configuration resolved for one customer-service agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    config: dict[str, object] = Field(default_factory=dict)


@runtime_checkable
class RuntimeRegistry(Protocol):
    """Runtime lookup boundary; the Redis adapter is implemented in stage 6."""

    async def get_active_runtime_version(self, agent_id: str) -> str:
        """Return the version to pin when a new session is created."""
        ...

    async def get_runtime_profile(
        self,
        agent_id: str,
        runtime_version: str,
    ) -> RuntimeProfile:
        """Load one immutable profile, including its tenant ownership."""
        ...
