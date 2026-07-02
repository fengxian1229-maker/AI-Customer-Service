from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

LLMRouteLiteral = Literal[
    "faq",
    "sop",
    "human_handoff",
    "emotion_care",
    "final_reply",
]


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
    detected_language: str
    language_confidence: float
    preserved_entities: list[str]
    missing_or_ambiguous: list[str]
    risk_flags: list[str]
    confidence: float
    reason: str
    provider: str
    mode: str


LLMRewriteAuthoritativeInput = LLMRewriteShadowInput
LLMRewriteAuthoritativeOutput = LLMRewriteShadowOutput


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


class LLMIntentClassificationInput(TypedDict, total=False):
    tenant_id: str
    conversation_id: str
    raw_user_input: str
    rewritten_question: str | None
    reply_language: str | None
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


class LLMIntentClassificationOutput(TypedDict, total=False):
    intent: str
    route: str
    confidence: float
    sop_name: str | None
    faq_query: str | None
    risk_level: str | None
    requires_human: bool
    requires_backend: bool
    missing_slots: list[str]
    workflow_relation: str | None
    preserve_active_workflow: bool
    reason: str
    provider: str
    mode: str


class LLMSopSlotExtractionInput(TypedDict, total=False):
    intent: str
    current_slot_memory: dict[str, Any]
    latest_user_text: str
    attachments_summary: list[dict[str, Any]]
    recent_messages: list[dict[str, Any]]
    language: str


class LLMSopDialoguePlannerInput(TypedDict, total=False):
    tenant_id: str
    conversation_id: str
    sop_name: str
    active_workflow: str | None
    workflow_stage: str | None
    current_slot_memory: dict[str, Any]
    sop_definition: dict[str, Any]
    latest_user_text: str
    attachments_summary: list[dict[str, Any]]
    recent_messages: list[dict[str, Any]]
    reply_language: str


class LLMFinalReplyInput(TypedDict, total=False):
    tenant_id: str
    channel_type: str
    conversation_id: str
    raw_user_input: str
    rewritten_question: str | None
    recent_messages: list[dict[str, Any]]
    route: str | None
    intent_result: dict[str, Any] | None
    active_workflow: str | None
    workflow_stage: str | None
    status: str | None
    slot_memory: dict[str, Any]
    missing_slots: list[str]
    sop_action: str | None
    rag_result: dict[str, Any] | None
    backend_result: dict[str, Any] | None
    node_reply_template: str | None
    node_reply_instruction: str | None
    node_facts: dict[str, Any]
    detected_language: str | None
    language_confidence: float | None
    language_source: str | None
    conversation_language: str | None
    reply_language: str | None
    language_result: dict[str, Any] | None
    supported_languages: list[str]
    response_text_fallback: str
    reply_plan: dict[str, Any]
    commands: list[dict[str, Any]]
    tenant_persona: dict[str, Any]


class LLMSopSlotExtractionOutput(TypedDict, total=False):
    intent: str
    extracted_slots: dict[str, str | None]
    attachment_classification: dict[str, Any]
    missing_slots: list[str]
    confidence: dict[str, float]
    reason: str
    provider: str
    mode: str


class LLMSopDialoguePlannerOutput(TypedDict, total=False):
    intent_relation: str
    extracted_slots: dict[str, Any]
    slot_updates: dict[str, Any]
    slot_confidence: dict[str, float]
    missing_slots: list[str]
    should_ask_confirmation: bool
    reply_draft: str
    reason: str
    provider: str
    mode: str


class LLMFinalReplyOutput(TypedDict, total=False):
    text: str
    language: str
    tone: str
    confidence: float
    safety_flags: list[str]
    used_facts: list[str]
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


class LLMRewriteAuthoritativeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rewritten_question: str
    normalized_query: str
    detected_language: str = "unknown"
    language: str = "unknown"
    language_confidence: float = 0.0
    preserved_entities: list[str] = Field(default_factory=list)
    missing_or_ambiguous: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str


class LLMIntentShadowSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    route: LLMRouteLiteral
    confidence: float = 0.0
    reason: str
    sop_name: str | None = None
    faq_query: str | None = None
    risk_level: str | None = None


class LLMIntentClassificationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    route: LLMRouteLiteral
    confidence: float = 0.0
    sop_name: str | None = None
    faq_query: str | None = None
    risk_level: str | None = None
    requires_human: bool = False
    requires_backend: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    workflow_relation: str | None = None
    preserve_active_workflow: bool = True
    reason: str


LLMRouterInput = LLMIntentClassificationInput
LLMRouterDecisionOutput = LLMIntentClassificationOutput
LLMRouterDecisionSchema = LLMIntentClassificationSchema


class LLMSopSlotExtractionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    extracted_slots: dict[str, str | None] = Field(default_factory=dict)
    attachment_classification: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)
    reason: str


class LLMSopDialoguePlannerSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_relation: str
    extracted_slots: dict[str, Any] = Field(default_factory=dict)
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    slot_confidence: dict[str, float] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    should_ask_confirmation: bool = False
    reply_draft: str = ""
    reason: str


class LLMFinalReplySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    language: str = "unknown"
    tone: str = "neutral"
    confidence: float = 0.0
    safety_flags: list[str] = Field(default_factory=list)
    used_facts: list[str] = Field(default_factory=list)
    reason: str
