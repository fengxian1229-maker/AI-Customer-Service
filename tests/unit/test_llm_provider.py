import pytest


def test_settings_default_to_full_llm_mode():
    from app.core.settings import Settings

    settings = Settings(
        livechat_agent_access_token="x",
        livechat_account_id="y",
        _env_file=None,
    )

    assert settings.llm_provider == "gemini"
    assert settings.llm_sop_slot_enabled is True
    assert settings.llm_final_reply_enabled is True
    assert settings.llm_intent_fallback_to_deterministic is True
    assert settings.llm_sop_slot_fallback_to_deterministic is True
    assert settings.llm_final_reply_fallback_enabled is True
    assert settings.livechat_thinking_indicator_enabled is False


def test_build_llm_provider_supports_off_and_mock():
    from app.llm.mock_provider import MockLLMProvider
    from app.llm.provider import build_llm_provider

    assert build_llm_provider("off") is None
    assert isinstance(build_llm_provider("mock"), MockLLMProvider)


def test_build_llm_provider_supports_gemini_with_settings():
    from app.core.settings import Settings
    from app.llm.gemini_provider import GeminiLLMProvider
    from app.llm.provider import build_llm_provider

    settings = Settings(
        livechat_agent_access_token="x",
        livechat_account_id="y",
    )

    class FakeGeminiProvider(GeminiLLMProvider):
        def __init__(self, settings) -> None:
            self.settings = settings

    import app.llm.provider as provider_module

    original = provider_module.GeminiLLMProvider
    provider_module.GeminiLLMProvider = FakeGeminiProvider
    try:
        assert isinstance(build_llm_provider("gemini", settings=settings), FakeGeminiProvider)
        assert isinstance(build_llm_provider("GEMINI", settings=settings), FakeGeminiProvider)
    finally:
        provider_module.GeminiLLMProvider = original


def test_build_llm_provider_requires_settings_for_gemini():
    from app.llm.provider import build_llm_provider

    with pytest.raises(ValueError, match="settings are required"):
        build_llm_provider("gemini")


def test_build_llm_provider_rejects_unknown_mode():
    from app.llm.provider import build_llm_provider

    with pytest.raises(ValueError, match="Unsupported llm provider"):
        build_llm_provider("openai")


def test_mock_provider_has_full_llm_surface_and_composes_final_reply():
    import asyncio

    from app.llm.mock_provider import MockLLMProvider

    provider = MockLLMProvider()

    assert hasattr(provider, "rewrite")
    assert hasattr(provider, "route")
    assert hasattr(provider, "extract_sop_slots")
    assert hasattr(provider, "compose_final_reply")

    final_reply = asyncio.run(
        provider.compose_final_reply(
            {
                "response_text_fallback": "请提供用户名或注册手机号以及存款付款截图。",
                "reply_language": "zh-Hans",
                "tenant_persona": {"tone": "polite"},
            }
        )
    )

    assert final_reply["text"] == "请提供用户名或注册手机号以及存款付款截图。"
    assert final_reply["confidence"] >= 0.70
    assert final_reply["provider"] == "mock"
    assert final_reply["mode"] == "final_reply"


def test_mock_provider_analyzes_image_attachment_as_candidate_only():
    import asyncio

    from app.llm.mock_provider import MockLLMProvider

    provider = MockLLMProvider()

    result = asyncio.run(
        provider.analyze_image_attachment(
            {
                "attachment_url": "https://cdn.example/deposit-receipt.png",
                "mime_type": "image/png",
                "filename": "deposit-receipt.png",
                "tenant_id": "default",
                "conversation_id": "livechat:chat-1",
            }
        )
    )

    assert result["candidate_intents"] == ["deposit_missing_candidate"]
    assert result["receipt_kind"] == "deposit"
    assert result["is_receipt_like"] is True
    assert result["provider"] == "mock"
    assert result["mode"] == "image_analysis_candidate"
