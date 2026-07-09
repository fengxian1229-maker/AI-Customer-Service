import json
from typing import Protocol

from app.llm.gemini_model import build_gemini_chat_model


class Translator(Protocol):
    def translate(self, text: str) -> str:
        ...


class NullTranslator:
    def translate(self, text: str) -> str:
        return text


class GeminiTraditionalChineseTranslator:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._model = None

    def translate(self, text: str) -> str:
        if not text.strip():
            return text
        if self._model is None:
            self._model = build_gemini_chat_model(self.settings)
        prompt = (
            "Translate the following customer-service chat text into Traditional Chinese. "
            "Preserve account IDs, phone numbers, names, brand names, filenames, JSON snippets, and [URL] exactly. "
            "Return only the translated text.\n\n"
            f"{text}"
        )
        response = self._model.invoke(prompt)
        return str(getattr(response, "content", response) or text)


def translate_batch_json(translator: Translator, values: list[str]) -> list[str]:
    return [translator.translate(value) for value in values]


def safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
