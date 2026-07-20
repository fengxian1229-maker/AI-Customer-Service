import pytest
from pydantic import ValidationError

from app_v2.infrastructure.llm.models import (
    FinalReplyComposition,
    IntentClassification,
    NormalizedTurn,
    SlotCandidate,
    WorkflowInterpretation,
)


@pytest.mark.parametrize(
    "model",
    [
        NormalizedTurn(
            normalized_text="user 123",
            standalone_text="check turnover for user 123",
            detected_language="en",
            language_confidence=0.99,
            preserved_entities=["user 123"],
            ambiguities=[],
        ),
        IntentClassification(intent="turnover_requirement_query", confidence=0.95, reason="explicit request"),
        WorkflowInterpretation(
            relation="supplement",
            slot_candidates=[
                SlotCandidate(
                    slot_name="account_or_phone",
                    value="user-123",
                    source_message_id="message-1",
                    confidence=0.98,
                )
            ],
            confidence=0.95,
            reason="account supplied",
        ),
        FinalReplyComposition(text="Please provide the account."),
    ],
)
def test_llm_contracts_round_trip(model):
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_intent_classification_rejects_route_or_tool_output():
    with pytest.raises(ValidationError):
        IntentClassification.model_validate(
            {
                "intent": "turnover_requirement_query",
                "confidence": 0.95,
                "reason": "explicit request",
                "route": "workflow",
                "tool": "backend_query",
            }
        )


@pytest.mark.parametrize(
    ("contract", "extra_field"),
    [
        (
            {
                "normalized_text": "hello",
                "standalone_text": "hello",
                "detected_language": "en",
                "language_confidence": 1.0,
                "preserved_entities": [],
                "ambiguities": [],
            },
            {"original_text": "hello"},
        ),
        ({"text": "hello"}, {"reply_language": "en"}),
        ({"text": "hello"}, {"response_kind": "direct_reply"}),
    ],
)
def test_llm_contracts_reject_removed_echo_or_policy_fields(contract, extra_field):
    model = NormalizedTurn if "normalized_text" in contract else FinalReplyComposition

    with pytest.raises(ValidationError):
        model.model_validate({**contract, **extra_field})
