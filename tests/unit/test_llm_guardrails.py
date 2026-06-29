import pytest


def test_validate_llm_route_rejects_invalid_route():
    from app.llm.guardrails import validate_llm_route

    with pytest.raises(ValueError, match="Unsupported llm route"):
        validate_llm_route("not_a_route")


def test_validate_llm_route_normalizes_common_aliases():
    from app.llm.guardrails import validate_llm_route

    assert validate_llm_route("SOP") == "sop"
    assert validate_llm_route("FAQ") == "faq"
    assert validate_llm_route("Human") == "human_handoff"
    assert validate_llm_route("human handoff") == "human_handoff"
    assert validate_llm_route("faq-then-sop") == "faq_then_sop"


def test_validate_llm_intent_rejects_invalid_intent():
    from app.llm.guardrails import validate_llm_intent

    with pytest.raises(ValueError, match="Unsupported llm intent"):
        validate_llm_intent("not_an_intent")


def test_validate_llm_intent_normalizes_common_aliases():
    from app.llm.guardrails import validate_llm_intent

    assert validate_llm_intent("deposit_inquiry") == "deposit_howto"
    assert validate_llm_intent("deposit-guide") == "deposit_howto"
    assert validate_llm_intent("reset password") == "forgot_password_howto"
    assert validate_llm_intent("withdraw") == "withdrawal_howto"


def _router_output(**overrides):
    output = {
        "rewritten_question": "I need a real support agent",
        "normalized_query": "I need a real support agent",
        "language": "en",
        "intent": "explicit_human_request",
        "route": "human_handoff",
        "confidence": 0.96,
        "requires_human": True,
        "requires_backend": False,
        "missing_slots": [],
        "preserved_entities": [],
        "reason": "The user wants a human.",
    }
    output.update(overrides)
    return output


def test_validate_router_decision_normalizes_invalid_handoff_intent_when_route_requires_human():
    from app.llm.guardrails import validate_router_decision_output

    decision = validate_router_decision_output(
        {},
        _router_output(intent="human_handoff_request"),
    )

    assert decision["intent"] == "explicit_human_request"
    assert decision["route"] == "human_handoff"

    specialist_decision = validate_router_decision_output(
        {},
        _router_output(intent="specialist_review"),
    )

    assert specialist_decision["intent"] == "explicit_human_request"
    assert specialist_decision["route"] == "human_handoff"


def test_validate_router_decision_forces_requires_human_for_handoff_route():
    from app.llm.guardrails import validate_router_decision_output

    decision = validate_router_decision_output(
        {},
        _router_output(
            intent="explicit_human_request",
            route="human_handoff",
            requires_human=False,
        ),
    )

    assert decision["route"] == "human_handoff"
    assert decision["intent"] == "explicit_human_request"
    assert decision["requires_human"] is True


def test_validate_router_decision_normalizes_handoff_intent_when_model_omits_requires_human():
    from app.llm.guardrails import validate_router_decision_output

    decision = validate_router_decision_output(
        {},
        _router_output(
            intent="human_handoff_request",
            route="human_handoff",
            requires_human=False,
        ),
    )

    assert decision["route"] == "human_handoff"
    assert decision["intent"] == "explicit_human_request"
    assert decision["requires_human"] is True


def test_validate_router_decision_normalizes_invalid_intent_for_clarification_and_unsupported_routes():
    from app.llm.guardrails import validate_router_decision_output

    clarification = validate_router_decision_output(
        {},
        _router_output(
            intent="unknown_x",
            route="clarification",
            requires_human=False,
        ),
    )
    unsupported = validate_router_decision_output(
        {},
        _router_output(
            intent="unknown_y",
            route="unsupported",
            requires_human=False,
        ),
    )

    assert clarification["intent"] == "clarification_needed"
    assert clarification["route"] == "clarification"
    assert unsupported["intent"] == "unsupported_concrete_issue"
    assert unsupported["route"] == "unsupported"


def test_validate_router_decision_rejects_invalid_intent_for_faq_route():
    from app.llm.guardrails import validate_router_decision_output

    with pytest.raises(ValueError, match="Unsupported llm intent"):
        validate_router_decision_output(
            {},
            _router_output(intent="unknown_x", route="faq", requires_human=False),
        )


def test_normalize_confidence_clamps_to_range():
    from app.llm.guardrails import normalize_confidence

    assert normalize_confidence(-0.5) == 0.0
    assert normalize_confidence(1.5) == 1.0


def test_normalize_risk_flags_deduplicates_in_stable_order():
    from app.llm.guardrails import normalize_risk_flags

    assert normalize_risk_flags(["backend_fact_like", "backend_fact_like", "user_fact_present"]) == [
        "backend_fact_like",
        "user_fact_present",
    ]


def test_validate_rewrite_output_enforces_active_workflow_backend_fact_and_attachment_flags():
    from app.llm.guardrails import validate_rewrite_output

    output = validate_rewrite_output(
        {
            "raw_user_input": "withdrawal status and balance",
            "active_workflow": "deposit_missing",
            "attachments_summary": [{"url": "https://cdn.example/file.png"}],
        },
        {
            "rewritten_question": "withdrawal status and balance",
            "normalized_query": "withdrawal status and balance",
            "language": "en",
            "preserved_entities": [],
            "missing_or_ambiguous": [],
            "risk_flags": ["backend_fact_like", "backend_fact_like"],
            "confidence": 0.8,
            "reason": "shadow",
        },
    )

    assert output["risk_flags"] == [
        "backend_fact_like",
        "active_workflow",
        "attachment_present",
    ]


def test_validate_rewrite_output_flags_spanish_missing_deposit_as_backend_fact_like():
    from app.llm.guardrails import validate_rewrite_output

    output = validate_rewrite_output(
        {
            "raw_user_input": "mi deposito no llegó",
            "active_workflow": None,
            "attachments_summary": [],
        },
        {
            "rewritten_question": "mi deposito no llegó",
            "normalized_query": "mi deposito no llegó",
            "language": "es",
            "preserved_entities": [],
            "missing_or_ambiguous": [],
            "risk_flags": [],
            "confidence": 0.9,
            "reason": "shadow",
        },
    )

    assert "backend_fact_like" in output["risk_flags"]
