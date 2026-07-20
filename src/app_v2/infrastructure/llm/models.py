from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


IntentName = Literal[
    "deposit_howto",
    "withdrawal_howto",
    "forgot_password_howto",
    "screenshot_upload_howto",
    "turnover_requirement_query",
    "explicit_human_request",
    "account_access_issue",
    "account_profile_or_wallet_change",
    "screenshot_upload_failed",
    "wallet_identity_risk",
    "account_verification_issue",
    "game_technical_issue",
    "abuse_or_fraud_risk",
    "unsupported_concrete_issue",
    "casual_chat",
    "service_frustration",
    "abusive_or_emotional",
    "clarification_needed",
    "backend_fact_like",
]

WorkflowRelation = Literal[
    "supplement",
    "resolved_or_cancel",
    "independent_faq",
    "switch_topic",
    "human_request",
    "acknowledgement",
    "contextual_followup",
    "unclear",
]


class NormalizedTurn(LLMContract):
    normalized_text: str
    standalone_text: str
    detected_language: str = Field(min_length=1)
    language_confidence: float = Field(ge=0, le=1)
    preserved_entities: list[str]
    ambiguities: list[str]


class IntentClassification(LLMContract):
    intent: IntentName
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class SlotCandidate(LLMContract):
    slot_name: str = Field(min_length=1)
    value: object
    source_message_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    correction_of_message_id: str | None = None


class WorkflowInterpretation(LLMContract):
    relation: WorkflowRelation
    slot_candidates: list[SlotCandidate]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class FinalReplyComposition(LLMContract):
    text: str = Field(min_length=1)
