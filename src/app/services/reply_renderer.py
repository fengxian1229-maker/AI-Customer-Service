from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.language_policy import normalize_language_code
from app.services.reply_intents import CustomerReplyIntent


DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "data" / "replies" / "default.json"


class ReplyRenderer:
    def __init__(self, template_path: str | Path | None = None, *, fallback_language: str = "zh-Hans") -> None:
        self.template_path = Path(template_path) if template_path else DEFAULT_TEMPLATE_PATH
        self.fallback_language = normalize_language_code(fallback_language)
        self._templates = self._load_templates()

    def render(
        self,
        reply_intent: CustomerReplyIntent | str,
        *,
        facts: dict[str, Any] | None = None,
        reply_language: str | None = None,
        tenant_persona: dict[str, Any] | None = None,
    ) -> str:
        del tenant_persona
        language = self._resolve_language(reply_language)
        intent = str(reply_intent)
        template = self._template(language, intent)
        return template.format_map(_SafeFacts(_format_facts(facts or {})))

    def _resolve_language(self, reply_language: str | None) -> str:
        normalized = normalize_language_code(reply_language)
        if normalized != "unknown" and normalized in self._templates:
            return normalized
        if self.fallback_language != "unknown" and self.fallback_language in self._templates:
            return self.fallback_language
        return next(iter(self._templates), "zh-Hans")

    def _template(self, language: str, intent: str) -> str:
        language_templates = self._templates.get(language) or {}
        if intent in language_templates:
            return str(language_templates[intent])
        fallback_templates = self._templates.get(self.fallback_language) or self._templates.get("zh-Hans") or {}
        if intent in fallback_templates:
            return str(fallback_templates[intent])
        return str(fallback_templates.get(CustomerReplyIntent.CLARIFICATION.value) or "请补充你要咨询的问题，我们会继续协助。")

    def _load_templates(self) -> dict[str, dict[str, str]]:
        with self.template_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return {
            str(language): {str(intent): str(template) for intent, template in templates.items()}
            for language, templates in raw.items()
            if isinstance(templates, dict)
        }


class _SafeFacts(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render_customer_reply(
    reply_intent: CustomerReplyIntent | str,
    *,
    facts: dict[str, Any] | None = None,
    reply_language: str | None = None,
    tenant_persona: dict[str, Any] | None = None,
    renderer: ReplyRenderer | None = None,
) -> str:
    return (renderer or ReplyRenderer()).render(
        reply_intent,
        facts=facts,
        reply_language=reply_language,
        tenant_persona=tenant_persona,
    )


def _format_facts(facts: dict[str, Any]) -> dict[str, str]:
    formatted = {}
    for key, value in facts.items():
        if value is None:
            formatted[key] = ""
        elif isinstance(value, float) and value.is_integer():
            formatted[key] = str(int(value))
        else:
            formatted[key] = str(value)
    return formatted
