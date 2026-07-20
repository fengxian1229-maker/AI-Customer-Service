import asyncio

import pytest
from pydantic import ValidationError

from app_v2.runtime.registry import RuntimeProfile, RuntimeRegistry


class FakeRuntimeRegistry:
    async def get_active_runtime_version(self, agent_id: str) -> str:
        assert agent_id == "agent-1"
        return "runtime-v1"

    async def get_runtime_profile(
        self,
        agent_id: str,
        runtime_version: str,
    ) -> RuntimeProfile:
        return RuntimeProfile(
            agent_id=agent_id,
            tenant_id="tenant-1",
            runtime_version=runtime_version,
            config={"default_reply_language": "en"},
        )


def test_runtime_registry_is_an_agent_scoped_structural_boundary():
    registry = FakeRuntimeRegistry()

    assert isinstance(registry, RuntimeRegistry)

    async def resolve_profile() -> RuntimeProfile:
        runtime_version = await registry.get_active_runtime_version("agent-1")
        return await registry.get_runtime_profile("agent-1", runtime_version)

    profile = asyncio.run(resolve_profile())

    assert profile.agent_id == "agent-1"
    assert profile.tenant_id == "tenant-1"
    assert profile.runtime_version == "runtime-v1"


def test_runtime_profile_identity_is_required_and_immutable():
    profile = RuntimeProfile(
        agent_id="agent-1",
        tenant_id="tenant-1",
        runtime_version="runtime-v1",
    )

    with pytest.raises(ValidationError):
        profile.runtime_version = "runtime-v2"


def test_runtime_profile_rejects_unknown_top_level_fields():
    with pytest.raises(ValidationError):
        RuntimeProfile(
            agent_id="agent-1",
            tenant_id="tenant-1",
            runtime_version="runtime-v1",
            active=True,
        )
