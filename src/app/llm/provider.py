from app.llm.gemini_provider import GeminiLLMProvider
from app.llm.mock_provider import MockLLMProvider


def build_llm_provider(mode: str, settings=None):
    normalized = (mode or "off").lower()
    if normalized == "off":
        return None
    if normalized == "mock":
        return MockLLMProvider()
    if normalized == "gemini":
        if settings is None:
            raise ValueError("Gemini llm provider settings are required.")
        return GeminiLLMProvider(settings)
    raise ValueError(f"Unsupported llm provider: {mode}")
