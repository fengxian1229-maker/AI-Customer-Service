import asyncio


def test_gemini_provider_rewrite_returns_shadow_output(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["rewrite_payload"] = payload
            return type(
                "RewriteResponse",
                (),
                {
                    "rewritten_question": "withdrawal status order 123",
                    "normalized_query": "withdrawal status order 123",
                    "language": "en",
                    "preserved_entities": ["123"],
                    "missing_or_ambiguous": [],
                    "risk_flags": ["backend_fact_like"],
                    "confidence": 0.91,
                    "reason": "Preserved order reference and backend-fact risk.",
                    "model_dump": lambda self: {
                        "rewritten_question": "withdrawal status order 123",
                        "normalized_query": "withdrawal status order 123",
                        "language": "en",
                        "preserved_entities": ["123"],
                        "missing_or_ambiguous": [],
                        "risk_flags": ["backend_fact_like"],
                        "confidence": 0.91,
                        "reason": "Preserved order reference and backend-fact risk.",
                    },
                },
            )()

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            captured["rewrite_schema"] = schema
            captured["rewrite_method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr(
        "app.llm.gemini_provider.build_gemini_chat_model",
        lambda settings: FakeModel(),
    )

    provider = GeminiLLMProvider(
        Settings(
            livechat_agent_access_token="x",
            livechat_account_id="y",
        )
    )
    result = asyncio.run(
        provider.rewrite(
            {
                "raw_user_input": "withdrawal status order 123",
                "current_rewritten_question": "withdrawal status order 123",
                "active_workflow": "deposit_missing",
            }
        )
    )

    assert captured["rewrite_method"] == "json_schema"
    assert result["provider"] == "gemini"
    assert result["mode"] == "shadow"
    assert result["risk_flags"] == ["backend_fact_like"]
    assert "Do not answer the customer." in captured["rewrite_payload"]["system_instruction"]


def test_gemini_provider_intent_returns_shadow_output(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["intent_payload"] = payload
            return type(
                "IntentResponse",
                (),
                {
                    "intent": "withdrawal_missing",
                    "route": "faq",
                    "confidence": 0.42,
                    "reason": "Model suspects FAQ, but this is shadow only.",
                    "sop_name": None,
                    "faq_query": None,
                    "risk_level": "elevated",
                    "model_dump": lambda self: {
                        "intent": "withdrawal_missing",
                        "route": "faq",
                        "confidence": 0.42,
                        "reason": "Model suspects FAQ, but this is shadow only.",
                        "sop_name": None,
                        "faq_query": None,
                        "risk_level": "elevated",
                    },
                },
            )()

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            captured["intent_schema"] = schema
            captured["intent_method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr(
        "app.llm.gemini_provider.build_gemini_chat_model",
        lambda settings: FakeModel(),
    )

    provider = GeminiLLMProvider(
        Settings(
            livechat_agent_access_token="x",
            livechat_account_id="y",
        )
    )
    result = asyncio.run(
        provider.classify_intent(
            {
                "raw_user_input": "where is my withdrawal",
                "deterministic_route": "sop",
                "active_workflow": "withdrawal_missing",
            }
        )
    )

    assert captured["intent_method"] == "json_schema"
    assert result["provider"] == "gemini"
    assert result["mode"] == "shadow"
    assert result["route"] == "faq"
    assert "Do not generate tool calls or external commands." in captured["intent_payload"]["system_instruction"]
