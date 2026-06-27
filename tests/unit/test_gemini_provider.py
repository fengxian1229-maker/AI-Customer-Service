import asyncio
import pytest


def test_gemini_provider_rewrite_returns_shadow_output(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["rewrite_payload"] = payload
            return {
                "rewritten_question": "withdrawal status order 123",
                "normalized_query": "withdrawal status order 123",
                "language": "en",
                "preserved_entities": ["123"],
                "missing_or_ambiguous": [],
                "risk_flags": ["backend_fact_like"],
                "confidence": 0.91,
                "reason": "Preserved order reference and backend-fact risk.",
            }

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
    assert result["risk_flags"] == ["backend_fact_like", "active_workflow"]
    assert captured["rewrite_payload"][0][0] == "system"
    assert "Do not answer the customer." in captured["rewrite_payload"][0][1]
    assert captured["rewrite_payload"][1][0] == "human"


def test_gemini_provider_intent_returns_shadow_output(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["intent_payload"] = payload
            return {
                "intent": "withdrawal_missing",
                "route": "faq",
                "confidence": 0.42,
                "reason": "Model suspects FAQ, but this is shadow only.",
                "sop_name": None,
                "faq_query": None,
                "risk_level": "elevated",
            }

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
    assert captured["intent_payload"][0][0] == "system"
    assert "Do not generate tool calls or external commands." in captured["intent_payload"][0][1]
    assert captured["intent_payload"][1][0] == "human"


def test_gemini_provider_rejects_invalid_route(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            return {
                "intent": "withdrawal_missing",
                "route": "invalid_route",
                "confidence": 0.42,
                "reason": "bad route",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))
    with pytest.raises(ValueError, match="Unsupported llm route"):
        asyncio.run(provider.classify_intent({"raw_user_input": "where is my withdrawal"}))


def test_gemini_provider_rejects_invalid_intent(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            return {
                "intent": "invalid_intent",
                "route": "faq",
                "confidence": 0.42,
                "reason": "bad intent",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))
    with pytest.raises(ValueError, match="Unsupported llm intent"):
        asyncio.run(provider.classify_intent({"raw_user_input": "where is my withdrawal"}))


def test_gemini_provider_clamps_confidence(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    responses = [
        {
            "rewritten_question": "how to deposit",
            "normalized_query": "how to deposit",
            "language": "en",
            "preserved_entities": [],
            "missing_or_ambiguous": [],
            "risk_flags": [],
            "confidence": 1.5,
            "reason": "too high",
        },
        {
            "intent": "faq_general",
            "route": "faq",
            "confidence": -0.5,
            "reason": "too low",
        },
    ]

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            return responses.pop(0)

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))
    rewrite = asyncio.run(provider.rewrite({"raw_user_input": "how to deposit"}))
    intent = asyncio.run(provider.classify_intent({"raw_user_input": "how to deposit"}))

    assert rewrite["confidence"] == 1.0
    assert intent["confidence"] == 0.0


def test_gemini_provider_missing_required_field_has_readable_error(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            return {
                "normalized_query": "how to deposit",
                "language": "en",
                "risk_flags": [],
                "confidence": 0.6,
                "reason": "missing field",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))
    with pytest.raises(ValueError, match="Missing required rewrite shadow field"):
        asyncio.run(provider.rewrite({"raw_user_input": "how to deposit"}))


def test_gemini_provider_route_uses_prompt_and_mode_from_router_mode(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import (
        FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT,
        GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT,
        GeminiLLMProvider,
    )

    captured = {"prompts": []}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["prompts"].append(payload[0][1])
            return {
                "rewritten_question": "怎么存款？",
                "normalized_query": "怎么存款",
                "language": "zh",
                "intent": "deposit_howto",
                "route": "FAQ",
                "confidence": 0.95,
                "faq_query": "怎么存款",
                "reason": "faq smoke",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())
    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    faq = asyncio.run(provider.route({"raw_user_input": "怎么存款？", "router_mode": "faq_authoritative"}))
    guarded = asyncio.run(provider.route({"raw_user_input": "怎么存款？", "router_mode": "guarded_authoritative"}))

    assert faq["mode"] == "faq_authoritative"
    assert guarded["mode"] == "guarded_authoritative"
    assert captured["prompts"] == [
        FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT,
        GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT,
    ]
