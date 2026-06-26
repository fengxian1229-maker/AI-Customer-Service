from app.llm.mock_provider import MockLLMProvider


def build_llm_provider(mode: str):
    normalized = (mode or "off").lower()
    if normalized == "off":
        return None
    if normalized == "mock":
        return MockLLMProvider()
    raise ValueError(f"Unsupported llm provider: {mode}")
