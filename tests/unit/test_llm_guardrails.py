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
    assert validate_llm_intent("human_handoff") == "explicit_human_request"
    assert validate_llm_intent("human") == "explicit_human_request"
    assert validate_llm_intent("human_agent") == "explicit_human_request"
    assert validate_llm_intent("human_support") == "explicit_human_request"
    assert validate_llm_intent("transfer_to_human") == "explicit_human_request"
    assert validate_llm_intent("handoff") == "explicit_human_request"
    assert validate_llm_intent("escalation") == "service_frustration"
    assert validate_llm_intent("escalate") == "service_frustration"


def test_validate_router_decision_accepts_human_handoff_intent_alias():
    from app.llm.guardrails import validate_router_decision_output

    decision = validate_router_decision_output(
        {},
        {
            "rewritten_question": "I need a real support agent",
            "normalized_query": "I need a real support agent",
            "language": "en",
            "intent": "human_handoff",
            "route": "human_handoff",
            "confidence": 0.96,
            "requires_human": True,
            "requires_backend": False,
            "missing_slots": [],
            "preserved_entities": [],
            "reason": "The user wants a human.",
        },
    )

    assert decision["intent"] == "explicit_human_request"
    assert decision["route"] == "human_handoff"


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
