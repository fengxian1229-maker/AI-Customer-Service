import asyncio
import json
import pytest
from datetime import datetime, date
from decimal import Decimal


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
                "route": "sop",
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
    assert result["route"] == "sop"
    assert captured["intent_payload"][0][0] == "system"
    assert "Do not generate tool calls or external commands." in captured["intent_payload"][0][1]
    assert captured["intent_payload"][1][0] == "human"


def test_gemini_provider_analyzes_image_attachment(monkeypatch):
    from app.core.settings import Settings
    from app.llm.contracts import LLMImageAttachmentAnalysisSchema
    from app.llm.gemini_provider import GeminiLLMProvider

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["image_payload"] = payload
            return {
                "candidate_intents": ["withdrawal_missing_candidate"],
                "candidate_slots": {},
                "receipt_kind": "withdrawal",
                "is_receipt_like": True,
                "confidence": 0.91,
                "evidence_summary": "The image looks like a withdrawal receipt.",
                "safety_flags": [],
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            captured["image_schema"] = schema
            captured["image_method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(
        provider.analyze_image_attachment(
            {
                "attachment_url": "https://cdn.example/withdrawal.png",
                "mime_type": "image/png",
                "filename": "withdrawal.png",
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
            }
        )
    )

    assert captured["image_schema"] is LLMImageAttachmentAnalysisSchema
    assert captured["image_method"] == "json_schema"
    assert captured["image_payload"][0][0] == "system"
    assert "candidate_only" in captured["image_payload"][0][1]
    assert captured["image_payload"][1][0] == "human"
    content_blocks = captured["image_payload"][1][1]
    assert isinstance(content_blocks, list)
    assert content_blocks[0]["type"] == "text"
    metadata = json.loads(content_blocks[0]["text"])
    assert metadata["tenant_id"] == "default"
    assert metadata["conversation_id"] == "livechat:chat-1"
    assert metadata["mime_type"] == "image/png"
    assert metadata["active_workflow"] is None
    assert metadata["workflow_stage"] is None
    assert content_blocks[1] == {
        "type": "image_url",
        "image_url": {"url": "https://cdn.example/withdrawal.png"},
    }
    assert result["candidate_intents"] == ["withdrawal_missing_candidate"]
    assert result["receipt_kind"] == "withdrawal"
    assert result["provider"] == "gemini"
    assert result["mode"] == "image_analysis_candidate"


def test_gemini_provider_image_analysis_missing_url_returns_unknown(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            raise AssertionError("model should not be called when attachment_url is missing")

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(
        provider.analyze_image_attachment(
            {
                "attachment_url": None,
                "mime_type": "image/png",
                "filename": "missing.png",
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
            }
        )
    )

    assert result["candidate_intents"] == ["unknown_image"]
    assert result["receipt_kind"] == "unknown"
    assert result["is_receipt_like"] is False
    assert result["safety_flags"] == ["missing_attachment_url", "candidate_only"]


def test_gemini_provider_image_analysis_multimodal_error_returns_unknown(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            raise RuntimeError("download failed")

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(
        provider.analyze_image_attachment(
            {
                "attachment_url": "https://cdn.example/broken.png",
                "mime_type": "image/png",
                "filename": "broken.png",
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
            }
        )
    )

    assert result["candidate_intents"] == ["unknown_image"]
    assert result["receipt_kind"] == "unknown"
    assert result["safety_flags"] == ["image_download_or_multimodal_error", "candidate_only"]


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
            "intent": "deposit_howto",
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


def test_gemini_provider_route_accepts_casual_chat_for_greeting(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            return {
                "intent": "casual_chat",
                "route": "final_reply",
                "confidence": 0.95,
                "requires_human": False,
                "requires_backend": False,
                "missing_slots": [],
                "workflow_relation": "none",
                "preserve_active_workflow": True,
                "reason": "The user only says hello.",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())
    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(provider.route({"raw_user_input": "你好", "router_mode": "guarded_authoritative"}))

    assert result["route"] == "final_reply"
    assert result["intent"] == "casual_chat"


def test_gemini_provider_faq_targets_are_limited_to_canonical_howto_intents():
    from app.llm.gemini_provider import (
        FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT,
        FAQ_KNOWLEDGE_TARGETS,
        GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT,
    )

    for expected in (
        "deposit_howto",
        "withdrawal_howto",
        "forgot_password_howto",
        "screenshot_upload_howto",
    ):
        assert expected in FAQ_KNOWLEDGE_TARGETS
        assert expected in FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
        assert expected in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT

    for banned in (
        "rollover_explanation",
        "menu_help",
        "faq_general",
        "流水要求说明",
        "菜单导航帮助",
        "奖金规则说明",
        "账户安全说明",
    ):
        assert banned not in FAQ_KNOWLEDGE_TARGETS
        assert banned not in FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
        assert banned not in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT


def test_guarded_intent_prompt_prioritizes_business_request_over_emotion_care():
    from app.llm.gemini_provider import GUARDED_AUTHORITATIVE_INTENT_CLASSIFIER_SYSTEM_PROMPT

    prompt = GUARDED_AUTHORITATIVE_INTENT_CLASSIFIER_SYSTEM_PROMPT

    assert "emotion_care is only for emotion/frustration/abusive language as the primary issue" in prompt
    assert "If ordinary emotional language appears together with a concrete business request, choose the business route first" in prompt
    assert "fraud, scam, fund-safety, account-safety, or severe abuse concerns" in prompt


def test_gemini_provider_guarded_prompt_allows_casual_chat_for_greetings():
    from app.llm.gemini_provider import GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT

    assert "- casual_chat" in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
    assert "- final_reply" in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
    assert "- casual_chat" in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT.split("Allowed intents", 1)[1]
    assert "without a service request" in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
    assert "use route: final_reply and intent: casual_chat" in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
    assert "你好，我想提款" not in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT


def test_gemini_provider_guarded_prompt_keeps_sop_boundary_rules_out_of_faq_targets():
    from app.llm.gemini_provider import GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT

    expected_boundaries = (
        "deposit_missing",
        "withdrawal_missing",
        "withdrawal_blocked_or_rollover",
        "pending_reply_lookup",
        "human_handoff",
    )
    for boundary in expected_boundaries:
        assert boundary in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT

    assert "For pure \"what is rollover/流水\" explanation, use route: faq" not in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
    assert "For ordinary how-to, manual, guide, navigation, or concept explanation questions, use route: faq." not in GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT


def test_gemini_provider_extract_sop_slots_uses_structured_output(monkeypatch):
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider, SOP_SLOT_EXTRACTOR_SYSTEM_PROMPT

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, payload):
            captured["payload"] = payload
            return {
                "intent": "deposit_missing",
                "extracted_slots": {"account_or_phone": "andy123", "amount": "500"},
                "attachment_classification": {},
                "missing_slots": ["deposit_screenshot"],
                "confidence": {"account_or_phone": 0.9, "amount": 0.8},
                "reason": "slots",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            captured["schema"] = schema
            captured["method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.gemini_provider.build_gemini_chat_model", lambda settings: FakeModel())

    provider = GeminiLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))
    result = asyncio.run(
        provider.extract_sop_slots(
            {
                "intent": "deposit_missing",
                "latest_user_text": "usuario andy123 monto 500",
                "attachments_summary": [],
                "current_slot_memory": {},
            }
        )
    )

    assert captured["method"] == "json_schema"
    assert captured["payload"][0][1] == SOP_SLOT_EXTRACTOR_SYSTEM_PROMPT
    assert result["provider"] == "gemini"
    assert result["mode"] == "sop_slot"
    assert result["extracted_slots"]["account_or_phone"] == "andy123"


def test_gemini_provider_build_chat_messages_serializes_datetime_recent_messages():
    import json

    from app.llm.gemini_provider import _build_chat_messages

    messages = _build_chat_messages(
        "system prompt",
        {
            "raw_user_input": "怎么存款？",
            "recent_messages": [
                {
                    "created_at": datetime(2026, 6, 27, 1, 2, 3),
                    "business_date": date(2026, 6, 27),
                    "amount": Decimal("10.50"),
                }
            ],
        },
    )

    payload = json.loads(messages[1][1])

    assert payload["recent_messages"][0]["created_at"] == "2026-06-27 01:02:03"
    assert payload["recent_messages"][0]["business_date"] == "2026-06-27"
    assert payload["recent_messages"][0]["amount"] == "10.50"
    assert "怎么存款？" in messages[1][1]
