from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from app.llm.contracts import LLMFinalReplyInput, LLMFinalReplyOutput, LLMFinalReplySchema
from app.llm.gemini_model import build_gemini_chat_model

FINAL_REPLY_SYSTEM_PROMPT = """You are the Final Reply Composer for a customer service system.

Your only job is to produce the final user-visible customer service wording.
You may polish tone, language, brevity, and empathy.
You must not change route, intent, status, workflow_stage, slot_memory, commands, or backend actions.
You must not decide account, order, payment, deposit, withdrawal, balance, refund, rejection, or completion facts.
You must not add unverified facts such as success, failure, credited, rejected, refunded, completed, or processed.
You must preserve reply_plan.must_say and avoid reply_plan.must_not_say.
For ask_missing_slots, ask for every slot listed in missing_slots.
For backend waiting, do not promise an outcome or timing.
For human handoff, you may say you will request/arrange transfer, but do not claim a human agent has already joined.
For FAQ answers, use only the supplied fallback/rag/reply_plan content. Do not add policies not present there.
Do not expose internal Telegram identifiers such as tg:21, mock_tg:21, telegram_case_id, or telegram_message_id.
Do not claim information was synced/sent/supplemented to backend unless the supplied commands include telegram.append_to_case.
You must reply in reply_language.
You must not choose another language unless reply_language is unknown.
If reply_language is unknown, use tenant_persona.default_language.
Your output JSON language field must equal the final language you used.
Do not mix languages unless the fallback response or user message explicitly mixes languages.
Do not translate account IDs, order IDs, amounts, URLs, usernames, phone numbers, or staff/backend facts.
Do not expose internal language detection fields to the user.

Supported language codes:
- zh-Hans: Simplified Chinese
- zh-Hant: Traditional Chinese
- en: English
- es: Spanish
- tl: Tagalog / Filipino
- th: Thai
- my: Burmese / Myanmar
- ms: Malay
- unknown: Unknown detection only; do not use for final reply unless no fallback language exists.

Return only structured JSON:
{
  "text": "...",
  "language": "...",
  "tone": "...",
  "confidence": 0.0,
  "safety_flags": [],
  "reason": "..."
}"""


class FinalReplyLLMProvider:
    provider_name = "gemini"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = build_gemini_chat_model(self.settings)
        return self._model

    async def compose_final_reply(self, payload: LLMFinalReplyInput) -> LLMFinalReplyOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMFinalReplySchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(
            [
                ("system", FINAL_REPLY_SYSTEM_PROMPT),
                ("human", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)),
            ]
        )
        result = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        return {
            "text": str(result.get("text") or ""),
            "language": result.get("language") or "unknown",
            "tone": result.get("tone") or "neutral",
            "confidence": float(result.get("confidence") or 0.0),
            "safety_flags": list(result.get("safety_flags") or []),
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
