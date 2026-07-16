from app.core.settings import Settings


def test_build_gemini_chat_model_passes_vertexai_settings(monkeypatch):
    from app.llm import gemini_model

    calls = {}

    class FakeChatGoogleGenerativeAI:
        def __init__(self, **kwargs) -> None:
            calls["kwargs"] = kwargs

    monkeypatch.setattr(gemini_model, "ChatGoogleGenerativeAI", FakeChatGoogleGenerativeAI)

    settings = Settings(
        livechat_agent_access_token="x",
        livechat_account_id="y",
    )

    model = gemini_model.build_gemini_chat_model(settings)

    assert isinstance(model, FakeChatGoogleGenerativeAI)
    assert calls["kwargs"] == {
        "model": "gemini-3.1-flash-lite",
        "project": "project-gemini-0306",
        "location": "global",
        "temperature": 1.0,
        "max_tokens": None,
        "timeout": 30.0,
        "max_retries": 2,
        "vertexai": True,
    }
