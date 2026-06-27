from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class LLMRewriteShadowInput(TypedDict, total=False):
    tenant_id: str
    conversation_id: str
    raw_user_input: str
    current_rewritten_question: str | None
    deterministic_rewrite_result: dict[str, Any] | None
    recent_messages: list[dict[str, Any]]
    active_workflow: str | None
    workflow_stage: str | None
    slot_memory: dict[str, Any]
    attachments_summary: list[dict[str, Any]]


class LLMRewriteShadowOutput(TypedDict, total=False):
    rewritten_question: str
    normalized_query: str
    language: str
    preserved_entities: list[str]
    missing_or_ambiguous: list[str]
    risk_flags: list[str]
    confidence: float
    reason: str
    provider: str
    mode: str


class LLMIntentShadowInput(TypedDict, total=False):
    tenant_id: str
    conversation_id: str
    raw_user_input: str
    rewritten_question: str | None
    llm_rewritten_question: str | None
    recent_messages: list[dict[str, Any]]
    deterministic_intent_result: dict[str, Any] | None
    deterministic_route: str | None
    active_workflow: str | None
    workflow_stage: str | None
    attachments_summary: list[dict[str, Any]]


class LLMRouterInput(TypedDict, total=False):
    router_mode: str | None
    mode: str | None
    tenant_id: str
    conversation_id: str
    raw_user_input: str
    deterministic_rewrite_result: dict[str, Any] | None
    deterministic_intent_result: dict[str, Any] | None
    deterministic_route: str | None
    recent_messages: list[dict[str, Any]]
    active_workflow: str | None
    workflow_stage: str | None
    slot_memory: dict[str, Any]
    attachments_summary: list[dict[str, Any]]


class LLMIntentShadowOutput(TypedDict, total=False):
    intent: str
    route: str
    confidence: float
    reason: str
    sop_name: str | None
    faq_query: str | None
    risk_level: str | None
    provider: str
    mode: str


class LLMRouterDecisionOutput(TypedDict, total=False):
    rewritten_question: str
    normalized_query: str | None
    language: str
    intent: str
    route: str
    confidence: float
    sop_name: str | None
    faq_query: str | None
    risk_level: str | None
    requires_human: bool
    requires_backend: bool
    missing_slots: list[str]
    preserved_entities: list[str]
    reason: str
    provider: str
    mode: str


class LLMRewriteShadowSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rewritten_question: str
    normalized_query: str
    language: str = "unknown"
    preserved_entities: list[str] = Field(default_factory=list)
    missing_or_ambiguous: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str


class LLMIntentShadowSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    route: str
    confidence: float = 0.0
    reason: str
    sop_name: str | None = None
    faq_query: str | None = None
    risk_level: str | None = None


class LLMRouterDecisionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rewritten_question: str
    normalized_query: str | None = None
    language: str = "unknown"
    intent: str
    route: str
    confidence: float = 0.0
    sop_name: str | None = None
    faq_query: str | None = None
    risk_level: str | None = None
    requires_human: bool = False
    requires_backend: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    preserved_entities: list[str] = Field(default_factory=list)
    reason: str
