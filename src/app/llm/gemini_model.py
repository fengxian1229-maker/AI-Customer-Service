from langchain_google_genai import ChatGoogleGenerativeAI


def build_gemini_chat_model(settings):
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        project=settings.gemini_project,
        location=settings.gemini_location,
        temperature=settings.gemini_temperature,
        max_tokens=settings.gemini_max_tokens,
        timeout=settings.gemini_timeout_seconds,
        max_retries=settings.gemini_max_retries,
        vertexai=settings.gemini_vertexai,
    )
