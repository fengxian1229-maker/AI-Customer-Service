from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from app.llm.contracts import LLMFinalReplyInput, LLMFinalReplyOutput, LLMFinalReplySchema
from app.llm.gemini_model import build_gemini_chat_model
from app.workflows.final_reply_templates import GLOBAL_FINAL_REPLY_CONSTRAINTS

FINAL_REPLY_SYSTEM_PROMPT = GLOBAL_FINAL_REPLY_CONSTRAINTS


class FinalReplyLLMProvider:
    provider_name = "gemini"

    def __init__(self, settings, *, model_name: str | None = None) -> None:
        self.settings = settings
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            if self.model_name:
                self._model = build_gemini_chat_model(self.settings, model_name=self.model_name)
            else:
                self._model = build_gemini_chat_model(self.settings)
        return self._model

    async def compose_final_reply(self, payload: LLMFinalReplyInput) -> LLMFinalReplyOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMFinalReplySchema,
            method="json_schema",
        )
        messages = [("system", FINAL_REPLY_SYSTEM_PROMPT)]
        node_instruction = str((payload or {}).get("node_reply_instruction") or "").strip()
        if node_instruction:
            messages.append(("system", node_instruction))
        messages.append(("human", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)))
        response = await structured_model.ainvoke(messages)
        result = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        return {
            "text": str(result.get("text") or ""),
            "language": result.get("language") or "unknown",
            "tone": result.get("tone") or "neutral",
            "confidence": float(result.get("confidence") or 0.0),
            "safety_flags": list(result.get("safety_flags") or []),
            "used_facts": list(result.get("used_facts") or []),
            "reason": result.get("reason") or "",
            "provider": self.provider_name,
            "mode": "final_reply",
        }


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
