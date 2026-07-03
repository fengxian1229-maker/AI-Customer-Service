from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.llm.gemini_model import build_gemini_chat_model
from app.services.chinese_script import adapt_chinese_script
from app.services.final_reply_service import FinalReplyService
from app.workflows.final_reply_policy import accepted_result, fallback_result, validate_final_reply_output
from app.workflows.final_reply_templates import (
    FINAL_REPLY_SEMANTIC_CONSTRAINTS,
    TEXT_ONLY_STREAMING_OUTPUT_INSTRUCTION,
)
from app.workflows.slot_extractors import normalize_text


class FinalReplyStreamingService:
    def __init__(self, settings, *, tenant_persona: dict[str, Any] | None = None) -> None:
        self.settings = settings
        self._model = None
        self.payload_builder = FinalReplyService(
            provider=None,
            enabled=False,
            tenant_persona={
                "default_language": getattr(settings, "tenant_persona_default_language", "zh-Hans"),
                "supported_languages": getattr(
                    settings,
                    "tenant_supported_languages",
                    "zh-Hans,zh-Hant,en,es,tl,th,my,ms",
                ),
                "tone": getattr(settings, "tenant_persona_tone", "polite"),
                "assistant_name": getattr(settings, "tenant_persona_assistant_name", None),
                "brand_name": getattr(settings, "tenant_persona_brand_name", None),
                **dict(tenant_persona or {}),
            },
        )

    @property
    def model(self):
        if self._model is None:
            self._model = build_gemini_chat_model(self.settings)
        return self._model

    async def stream_final_reply(self, state: dict[str, Any], preview_publisher) -> dict[str, Any]:
        fallback_text_raw = self._fallback_text(state, adapt=False)
        fallback_text = self._fallback_text(state, adapt=True)
        if not fallback_text:
            return {
                **state,
                "final_response_text": None,
                "final_reply_result": fallback_result("empty_fallback"),
            }

        payload = self.payload_builder._build_payload(state, fallback_text_raw or fallback_text)
        messages = self._build_messages(payload)
        accumulated_text = ""
        suppress_preview = False
        async for chunk in self.model.astream(messages):
            content = self._chunk_text(chunk)
            if not content:
                continue
            accumulated_text += content
            if self._looks_like_structured_output(accumulated_text):
                suppress_preview = True
                continue
            if not suppress_preview:
                await preview_publisher.publish_if_needed(accumulated_text)
        accumulated_text = self._normalize_streamed_text(accumulated_text)

        output = {
            "text": accumulated_text,
            "confidence": 0.8,
            "language": payload.get("reply_language") or state.get("reply_language") or "unknown",
            "tone": "polite",
            "safety_flags": [],
            "used_facts": [],
            "reason": "text_only_streaming",
        }
        violations = validate_final_reply_output(state, output)
        if violations:
            await preview_publisher.flush(fallback_text)
            return {
                **state,
                "response_text_fallback": fallback_text,
                "final_response_text": fallback_text,
                "final_reply_result": fallback_result("guardrail_failed", violations=violations),
            }
        await preview_publisher.flush(accumulated_text)
        return {
            **state,
            "response_text_fallback": fallback_text,
            "final_response_text": accumulated_text,
            "final_reply_result": accepted_result(output),
        }

    def _fallback_text(self, state: dict[str, Any], *, adapt: bool = True) -> str:
        reply_language = self.payload_builder._target_reply_language(state)
        fallback = normalize_text(state.get("response_text_fallback") or state.get("response_text"))
        if not adapt:
            return fallback
        return normalize_text(adapt_chinese_script(fallback, reply_language))

    def _build_messages(self, payload: dict[str, Any]) -> list[tuple[str, str]]:
        system_prompt = f"{FINAL_REPLY_SEMANTIC_CONSTRAINTS}\n\n{TEXT_ONLY_STREAMING_OUTPUT_INSTRUCTION}"
        messages = [("system", system_prompt)]
        node_instruction = str((payload or {}).get("node_reply_instruction") or "").strip()
        if node_instruction:
            messages.append(("system", node_instruction))
        messages.append(
            ("human", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default))
        )
        return messages

    def _chunk_text(self, chunk) -> str:
        if isinstance(chunk, str):
            return chunk
        content = getattr(chunk, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
            return "".join(parts)
        return str(content or "")

    def _looks_like_structured_output(self, text: str) -> bool:
        stripped = str(text or "").lstrip()
        return stripped.startswith("{") or stripped.startswith("```")

    def _normalize_streamed_text(self, text: str) -> str:
        text = normalize_text(text)
        parsed_text = self._extract_text_from_json_like(text)
        return normalize_text(parsed_text or text)

    def _extract_text_from_json_like(self, text: str) -> str | None:
        candidate = self._strip_json_fence(text)
        if not candidate.lstrip().startswith("{"):
            return None
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return normalize_text(parsed.get("text"))

    def _strip_json_fence(self, text: str) -> str:
        stripped = str(text or "").strip()
        if not stripped.startswith("```"):
            return stripped
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
