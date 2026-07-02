import json
from datetime import date, datetime
from decimal import Decimal

from app.llm.contracts import (
    LLMIntentShadowInput,
    LLMIntentShadowOutput,
    LLMIntentShadowSchema,
    LLMIntentClassificationOutput,
    LLMIntentClassificationInput,
    LLMIntentClassificationSchema,
    LLMSopDialoguePlannerInput,
    LLMSopDialoguePlannerOutput,
    LLMSopDialoguePlannerSchema,
    LLMRewriteAuthoritativeSchema,
    LLMRewriteShadowInput,
    LLMRewriteShadowOutput,
    LLMSopSlotExtractionInput,
    LLMSopSlotExtractionOutput,
    LLMSopSlotExtractionSchema,
)
from app.llm.guardrails import (
    validate_intent_output,
    validate_rewrite_output,
    validate_router_decision_output,
    validate_sop_dialogue_planner_output,
    validate_sop_slot_extraction_output,
)
from app.llm.gemini_model import build_gemini_chat_model

REWRITE_SYSTEM_PROMPT = """You are an authoritative rewrite model for an AI customer service routing system.
Your task is to normalize the user's message for downstream routing and retrieval.
You must preserve user-provided facts exactly, including usernames, phone numbers, order IDs, amounts, dates, and attachment references.
Detect the user's language with one of zh-Hans, zh-Hant, en, es, tl, th, my, ms, unknown.
Use unknown only when the message has no meaningful language signal.
Do not invent facts.
Do not answer the customer.
Do not decide real backend/account/payment/order facts.
Do not generate tool calls or external commands.
Return only structured JSON matching the schema."""

INTENT_SYSTEM_PROMPT = """You are an intent shadow model for an AI customer service routing system.
Your task is to output only a candidate intent classification for offline comparison.
You may suggest a candidate route, confidence, and short reason, but you do not control the real route.
Do not answer the customer.
Do not promise that anything was processed.
Do not generate tool calls or external commands.
Return only structured JSON matching the schema."""

FAQ_KNOWLEDGE_TARGETS = """FAQ knowledge targets. For FAQ route, choose the closest target and set intent and faq_query exactly as specified unless the question is genuinely outside the list:

1. 充值教程
   - route: faq
   - intent: deposit_howto
   - faq_query: 怎么存款
   - Use for: how to add money, fund account, put money into account, start playing after adding money, recharge, top up, cash in, deposit tutorial.

2. 提款教程
   - route: faq
   - intent: withdrawal_howto
   - faq_query: 如何提款
   - Use for: how to withdraw, withdrawal tutorial, cash out guide.

3. 忘记密码说明
   - route: faq
   - intent: forgot_password_howto
   - faq_query: 忘记密码
   - Use for: forgot password, reset password, cannot remember password.

4. 上传截图说明
   - route: faq
   - intent: screenshot_upload_howto
   - faq_query: 上传截图
   - Use for: how to upload/send screenshot or proof image.
"""

ALLOWED_INTENT_CONTRACT = """Allowed intents. The intent field must be exactly one of these values. Do not invent, translate, rename, or paraphrase intent names:
- deposit_howto
- withdrawal_howto
- forgot_password_howto
- screenshot_upload_howto
- deposit_missing
- withdrawal_missing
- withdrawal_blocked_or_rollover
- pending_reply_lookup
- account_access_issue
- account_profile_or_wallet_change
- explicit_human_request
- service_frustration
- abusive_or_emotional
- unsupported_concrete_issue
- clarification_needed
- acknowledgement
- contextual_followup
- casual_chat
- backend_fact_like
"""

GUARDED_AUTHORITATIVE_INTENT_CLASSIFIER_SYSTEM_PROMPT = f"""You are a guarded authoritative intent classifier for a customer service routing system.

Your only job is to classify the user's intent and choose the safest route metadata.

You must not answer the customer.
You must not rewrite, normalize, translate, summarize, or expand the customer's message.
You must not generate final customer replies.
You must not generate images.
You must not generate buttons.
You must not generate tool calls.
You must not generate external commands.
You must not decide real backend/account/payment/order facts.
You must not promise that anything was processed, credited, successful, or failed.

Allowed routes. The route field must be exactly one of these values:
- faq
- sop
- faq_then_sop
- human_handoff
- emotion_care
- clarification
- contextual_reply
- casual_chat
- unsupported

{ALLOWED_INTENT_CONTRACT}

{FAQ_KNOWLEDGE_TARGETS}

Routing rules:
- You are an intent classifier, not a reply writer and not a rewrite model.
- FAQ is static knowledge. SOP is stateful service handling.
- For ordinary how-to, manual, guide, or instruction questions that match the FAQ knowledge targets, use route: faq.
- For FAQ route, intent and faq_query must match one of the FAQ knowledge targets above.
- For user wording like "I just registered and want to put money into my account to start playing", use route: faq, intent: deposit_howto, faq_query: 怎么存款.
- For deposit/recharge/top-up/cash-in status, missing funds, payment not credited, or "I paid but it did not arrive", use route: sop and intent: deposit_missing.
- For withdrawal not received, use route: sop and intent: withdrawal_missing.
- For unable to withdraw, insufficient rollover/流水, checking whether rollover/流水 is enough, or asking to query rollover/流水, use route: sop or faq_then_sop and intent: withdrawal_blocked_or_rollover.
- For requests to check the previous case, prior reply, pending handling, or last ticket, use route: sop and intent: pending_reply_lookup.
- For account, order, payment, balance, deposit status, withdrawal status, or other backend fact-like requests, prefer SOP/human/backend-safe handling and set requires_backend: true when backend facts are needed.
- For escalation, take-over requests, specialist review requests, or cases where automated replies are not helping, use route: human_handoff, intent: explicit_human_request, requires_human: true.
- If the customer explicitly asks for a human, route must be human_handoff.
- For simple greetings or small talk without a service request, such as 你好, 您好, hi, or hello, use route: casual_chat and intent: casual_chat.
- If no route is safe because the message is too ambiguous, use route: clarification and intent: clarification_needed.

Workflow relation rules:
- Always set workflow_relation and preserve_active_workflow.
- If active_workflow is absent, set workflow_relation: none.
- If active_workflow is present, do not assume every new message must continue SOP.
- When active_workflow is present, classify the latest message relation to the current workflow:
  - current_workflow_supplement: the user is supplying account, phone, amount, order ID, screenshot/proof, payment channel, or concrete follow-up details for the current SOP. Use route: sop, intent: active_workflow, sop_name: active_workflow.
  - acknowledgement: the user only acknowledges or thanks, such as OK, 好的, 明白, 收到, thanks. Use route: contextual_reply, intent: acknowledgement, preserve_active_workflow: true. Do not treat this as a supplement.
  - contextual_followup: the user asks a question about the current requested information or whether another detail can substitute, such as "May I provide my name?". Use route: contextual_reply, intent: contextual_followup, preserve_active_workflow: true.
  - current_workflow_resolution: the user says the current active SOP is solved, arrived, credited, received, fixed, or no longer needs checking. Use route: sop, intent: active_workflow, sop_name: active_workflow, requires_backend: false, preserve_active_workflow: false.
  - independent_faq: the user temporarily asks a standalone FAQ. Use route: faq, canonical FAQ intent, faq_query, requires_backend: false, requires_human: false, preserve_active_workflow: true.
  - new_workflow_request: the user raises a different new SOP issue or mentions a different business object unrelated to the current workflow. Use route: clarification, intent: clarification_needed, preserve_active_workflow: true. Do not switch workflows directly.
  - human_escalation: the user explicitly asks for a human/manager/real support. Use route: human_handoff, intent: explicit_human_request, requires_human: true.
  - unclear: relation to the current workflow is unclear. Use route: clarification, intent: clarification_needed, preserve_active_workflow: true.
- If the business object conflicts with active_workflow, never output current_workflow_supplement or current_workflow_resolution. Example: active_workflow=withdrawal_missing but latest_user_text says deposit/deposito/存款; this is new_workflow_request unless the user explicitly says they meant the withdrawal case.
- Never clear, replace, or switch active_workflow. The system owns workflow state.

Examples when active_workflow=deposit_missing:
- "账号 abc123" -> route: sop, intent: deposit_missing, sop_name: deposit_missing, workflow_relation: current_workflow_supplement, preserve_active_workflow: true.
- "金额1000" -> route: sop, intent: deposit_missing, sop_name: deposit_missing, workflow_relation: current_workflow_supplement, preserve_active_workflow: true.
- "截图发给你了" -> route: sop, intent: deposit_missing, sop_name: deposit_missing, workflow_relation: current_workflow_supplement, preserve_active_workflow: true.
- "好的" -> route: contextual_reply, intent: acknowledgement, workflow_relation: acknowledgement, preserve_active_workflow: true.
- "May I provide my name?" -> route: contextual_reply, intent: contextual_followup, workflow_relation: contextual_followup, preserve_active_workflow: true.
- "ya llegó el depósito" -> route: sop, intent: deposit_missing, sop_name: deposit_missing, workflow_relation: current_workflow_resolution, preserve_active_workflow: false, requires_backend: false.
- "怎么提款？" -> route: faq, intent: withdrawal_howto, faq_query: 如何提款, workflow_relation: independent_faq, preserve_active_workflow: true.
- "我还有一笔提款没到账" -> route: clarification, intent: clarification_needed, workflow_relation: new_workflow_request, preserve_active_workflow: true.
- "我要人工" -> route: human_handoff, intent: explicit_human_request, workflow_relation: human_escalation, requires_human: true.

Example when active_workflow=withdrawal_missing:
- "Gracias.. ya llego el deposito" -> route: clarification, intent: clarification_needed, workflow_relation: new_workflow_request, preserve_active_workflow: true. Reason: the user mentions deposito/deposit while the active workflow is withdrawal_missing, so it must not be treated as a withdrawal case supplement.

Return only structured JSON matching the schema."""

GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_INTENT_CLASSIFIER_SYSTEM_PROMPT
FAQ_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT
ROUTER_SYSTEM_PROMPT = GUARDED_AUTHORITATIVE_ROUTER_SYSTEM_PROMPT

SOP_SLOT_EXTRACTOR_SYSTEM_PROMPT = """You are a SOP slot extraction node for a customer service system.

Your only job is to extract structured slots for deposit_missing or withdrawal_missing workflows.
Do not reply to the customer.
Do not generate tool calls.
Do not generate external commands.
Do not decide whether Telegram should be sent.
Do not promise that anything was processed, credited, successful, or failed.
Do not invent usernames, phone numbers, amounts, order IDs, or screenshot URLs.
Screenshot URLs must be selected only from attachments_summary or current_slot_memory.
If uncertain, return null for that slot and low confidence.
Return only structured JSON matching the schema."""

SOP_DIALOGUE_PLANNER_SYSTEM_PROMPT = """You are the LLM-first SOP dialogue understanding node for a customer service system.

Your job is to understand the latest customer message in the active SOP context.
You may extract slots, classify how the message relates to the current SOP, and draft a short follow-up reply.
You must not generate external commands, decide Telegram sends, decide backend facts, or bypass the SOP schema.
Only use slot keys declared in sop_definition. If a value is uncertain, omit it or use low confidence.
Attachment slots must use URLs from attachments_summary or current_slot_memory only.
Classify intent_relation as one of current_sop_supplement, faq_interrupt, new_issue, human_request, unclear.
The program owns slot merging, missing slot calculation, Telegram commands, safety validation, and idempotency.
Do not promise credited, completed, successful, failed, guaranteed, processed, or handled outcomes.
Return only structured JSON matching the schema."""


class GeminiLLMProvider:
    provider_name = "gemini"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = build_gemini_chat_model(self.settings)
        return self._model

    async def rewrite(self, payload: LLMRewriteShadowInput) -> LLMRewriteShadowOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMRewriteAuthoritativeSchema,
            method="json_schema",
        )
        raw = _model_dump(await structured_model.ainvoke(_build_chat_messages(REWRITE_SYSTEM_PROMPT, payload)))
        language = str(raw.get("detected_language") or raw.get("language") or "unknown")
        result = validate_rewrite_output(payload, {**raw, "language": language})
        return {
            "rewritten_question": result["rewritten_question"],
            "normalized_query": result["normalized_query"],
            "detected_language": language,
            "language": language,
            "language_confidence": float(raw.get("language_confidence") or 0.0),
            "preserved_entities": list(result.get("preserved_entities") or []),
            "missing_or_ambiguous": list(result.get("missing_or_ambiguous") or []),
            "risk_flags": list(result.get("risk_flags") or []),
            "confidence": float(result.get("confidence") or 0.0),
            "reason": result["reason"],
            "provider": self.provider_name,
            "mode": "shadow",
        }

    async def classify_intent(self, payload: LLMIntentShadowInput) -> LLMIntentShadowOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMIntentShadowSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(INTENT_SYSTEM_PROMPT, payload))
        result = validate_intent_output(payload, _model_dump(response))
        return {
            "intent": result["intent"],
            "route": result["route"],
            "confidence": float(result.get("confidence") or 0.0),
            "reason": result["reason"],
            "sop_name": result.get("sop_name"),
            "faq_query": result.get("faq_query"),
            "risk_level": result.get("risk_level"),
            "provider": self.provider_name,
            "mode": "shadow",
        }

    async def route(self, payload: LLMIntentClassificationInput) -> LLMIntentClassificationOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMIntentClassificationSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(ROUTER_SYSTEM_PROMPT, payload))
        result = validate_router_decision_output(payload, _model_dump(response))
        mode = str(payload.get("router_mode") or "guarded_authoritative")
        return {
            **result,
            "provider": self.provider_name,
            "mode": mode,
        }

    async def extract_sop_slots(self, payload: LLMSopSlotExtractionInput) -> LLMSopSlotExtractionOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMSopSlotExtractionSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(SOP_SLOT_EXTRACTOR_SYSTEM_PROMPT, payload))
        result = validate_sop_slot_extraction_output(payload, _model_dump(response))
        return {
            **result,
            "provider": self.provider_name,
            "mode": "sop_slot",
        }

    async def plan_sop_dialogue(self, payload: LLMSopDialoguePlannerInput) -> LLMSopDialoguePlannerOutput:
        structured_model = self.model.with_structured_output(
            schema=LLMSopDialoguePlannerSchema,
            method="json_schema",
        )
        response = await structured_model.ainvoke(_build_chat_messages(SOP_DIALOGUE_PLANNER_SYSTEM_PROMPT, payload))
        result = validate_sop_dialogue_planner_output(payload, _model_dump(response))
        return {
            **result,
            "provider": self.provider_name,
            "mode": "sop_dialogue_planner",
        }


def _model_dump(response) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    raise TypeError("Gemini structured output must be a dict-like schema result.")


def _build_chat_messages(system_prompt: str, payload: dict) -> list[tuple[str, str]]:
    return [
        ("system", system_prompt),
        ("human", _json_dumps_payload(payload)),
    ]


def _json_dumps_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
