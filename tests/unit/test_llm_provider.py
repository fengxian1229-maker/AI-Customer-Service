import pytest


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
