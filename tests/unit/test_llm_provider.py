import pytest


def test_build_llm_provider_supports_off_and_mock():
    from app.llm.mock_provider import MockLLMProvider
    from app.llm.provider import build_llm_provider

    assert build_llm_provider("off") is None
    assert isinstance(build_llm_provider("mock"), MockLLMProvider)


def test_build_llm_provider_rejects_unknown_mode():
    from app.llm.provider import build_llm_provider

    with pytest.raises(ValueError, match="Unsupported llm provider"):
        build_llm_provider("openai")
