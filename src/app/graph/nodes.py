from typing import Any
import re

from app.graph.state import GraphState
from app.llm.guardrails import validate_router_decision_output, validate_sop_dialogue_planner_output, validate_sop_slot_extraction_output
from app.schemas.events import InboundEvent
from app.workflows.command_contracts import CommandType
from app.services.rag import answer_from_rag_context, answer_from_static_knowledge
from app.services.language_policy import (
    detect_language_deterministic,
    normalize_language_code,
    parse_supported_languages,
    resolve_language_policy,
)
from app.workflows.slot_extractors import (
    attachment_urls,
    extract_amount,
    extract_identity,
    extract_order_id,
    is_explicit_human_request,
    normalize_text,
)
from app.workflows.sop_handlers import run_sop
from app.workflows.waiting_backend_classifier import handle_waiting_backend
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.llm_sop_dialogue_planner import build_llm_sop_dialogue_input
from app.workflows.sop_definitions import get_sop_definition


LLM_AUTHORITATIVE_SOURCES = {
    "llm_guarded_authoritative",
    "llm_guarded_authoritative_post_guard",
    "llm_rewrite_authoritative",
}

ACTIVE_WORKFLOW_GUARD_STAGES = {"waiting_backend", "backend_querying", "collecting_slots", "lookup_pending_reply"}


def build_graph_state_from_event(
    event: InboundEvent,
    conversation: dict[str, Any],
    recent_messages: list[dict[str, Any]] | None = None,
) -> GraphState:
    payload = event.payload_json or {}
    raw_input = _extract_text(payload)
    return {
        "tenant_id": conversation.get("tenant_id") or event.organization_id or "default",
        "channel_type": conversation.get("channel_type") or "livechat",
        "conversation_id": conversation.get("conversation_id") or f"livechat:{event.chat_id or 'unknown'}",
        "chat_id": event.chat_id or "unknown",
        "thread_id": event.thread_id,
        "raw_user_input": raw_input,
        "rewritten_question": None,
        "detected_language": None,
        "language_confidence": None,
        "language_source": None,
        "conversation_language": None,
        "reply_language": None,
        "supported_languages": [],
        "language_result": None,
        "event_type": event.standard_event_type,
        "attachments": _extract_attachments(payload, event.standard_event_type),
        "status": conversation.get("status") or "AI_ACTIVE",
        "active_workflow": conversation.get("active_workflow"),
        "workflow_stage": conversation.get("workflow_stage"),
        "slot_memory": dict(conversation.get("slot_memory") or {}),
        "llm_rewrite_result": None,
        "llm_router_result": None,
        "intent_result": None,
        "llm_intent_result": None,
        "route": None,
        "route_source": "deterministic",
        "route_locked": False,
        "rewrite_source": "deterministic",
        "rag_context": None,
        "rag_result": None,
        "recent_messages": recent_messages or [],
        "reply_plan": None,
        "response_text_fallback": None,
        "final_response_text": None,
        "final_reply_result": None,
        "response_text": None,
        "commands": [],
        "errors": [],
    }


def rewrite_question_node(state: GraphState) -> GraphState:
    if state.get("route_locked") and state.get("rewritten_question"):
        return state
    if state.get("rewrite_source") in LLM_AUTHORITATIVE_SOURCES and state.get("rewritten_question"):
        return state
    raw = normalize_text(state.get("raw_user_input"))
    identity = extract_identity(raw)
    language = detect_language_deterministic(raw)
    return {
        **state,
        "rewritten_question": raw,
        "rewrite_result": {
            "rewritten_question": raw,
            "language": language["detected_language"],
            "detected_language": language["detected_language"],
            "language_confidence": language["language_confidence"],
            "language_source": language["language_source"],
            "mentioned_entities": {
                "account_or_phone": identity["value"] if identity else None,
                "transaction_ref": None,
                "amount": extract_amount(raw),
                "date": None,
            },
            "notes": [],
        },
    }


def prepare_route_state(state: GraphState) -> GraphState:
    routed = rewrite_question_node(state)
    return intent_router_node(routed)


def make_rewrite_question_node(llm_rewrite_service=None, *, min_confidence: float = 0.0):
    async def node(state: GraphState) -> GraphState:
        if state.get("route_locked") and state.get("rewritten_question"):
            return state
        if state.get("rewrite_source") in LLM_AUTHORITATIVE_SOURCES and state.get("rewritten_question"):
            return state
        if not llm_rewrite_service or not hasattr(llm_rewrite_service, "rewrite"):
            return rewrite_question_node(state)

        payload = _build_llm_rewrite_payload(state)
        try:
            raw_result = await llm_rewrite_service.rewrite(payload)
            result = dict(raw_result or {})
            confidence = float(result.get("confidence") or result.get("language_confidence") or 0.0)
            if confidence < float(min_confidence or 0.0):
                return _deterministic_rewrite_fallback(state, "low_confidence", result=result)
            rewritten = normalize_text(result.get("rewritten_question") or state.get("raw_user_input"))
            normalized_query = normalize_text(result.get("normalized_query") or rewritten)
            language = result.get("detected_language") or result.get("language") or "unknown"
            language_confidence = float(result.get("language_confidence") or 0.0)
            rewrite_result = {
                "rewritten_question": rewritten,
                "normalized_query": normalized_query,
                "detected_language": language,
                "language": language,
                "language_confidence": language_confidence,
                "language_source": "llm_rewrite",
                "preserved_entities": list(result.get("preserved_entities") or []),
                "missing_or_ambiguous": list(result.get("missing_or_ambiguous") or []),
                "risk_flags": list(result.get("risk_flags") or []),
                "confidence": confidence,
                "reason": result.get("reason"),
                "source": "llm_rewrite_authoritative",
            }
            return {
                **state,
                "rewritten_question": rewritten,
                "rewrite_result": rewrite_result,
                "llm_rewrite_result": {**result, "status": result.get("status") or "accepted"},
                "rewrite_source": "llm_rewrite_authoritative",
            }
        except Exception as exc:
            return _deterministic_rewrite_fallback(state, "exception", exc=exc)

    return node


def language_policy_node(
    state: GraphState,
    *,
    language_detection_enabled: bool = True,
    language_detection_min_confidence: float = 0.70,
    tenant_persona_default_language: str = "zh-Hans",
    tenant_supported_languages: str | list[str] = "zh-Hans,zh-Hant,en,es,tl,th,my,ms",
    language_fallback: str = "zh-Hans",
    language_persist_to_slot_memory: bool = True,
) -> GraphState:
    if not language_detection_enabled:
        supported = parse_supported_languages(tenant_supported_languages)
        reply_language = _default_reply_language(tenant_persona_default_language, language_fallback, supported)
        slot_memory = dict(state.get("slot_memory") or {})
        if language_persist_to_slot_memory:
            slot_memory["last_reply_language"] = reply_language
        policy = {
            "detected_language": "unknown",
            "language_confidence": 0.0,
            "deterministic_language": "unknown",
            "deterministic_language_confidence": 0.0,
            "llm_language": "unknown",
            "llm_language_source": None,
            "llm_language_confidence": 0.0,
            "language_source": "tenant_default",
            "conversation_language": reply_language,
            "reply_language": reply_language,
            "supported_languages": supported,
            "reason": "language detection disabled",
            "detection_reason": "disabled",
        }
        state = {**state, "slot_memory": slot_memory}
    else:
        state_for_policy = {**state, "slot_memory": dict(state.get("slot_memory") or {})}
        policy = resolve_language_policy(
            state_for_policy,
            tenant_default_language=tenant_persona_default_language,
            supported_languages=tenant_supported_languages,
            min_confidence=language_detection_min_confidence,
            fallback_language=language_fallback,
            persist_to_slot_memory=language_persist_to_slot_memory,
        )
        state = {**state, "slot_memory": state_for_policy.get("slot_memory") or {}}
    return {
        **state,
        "detected_language": policy.get("detected_language"),
        "language_confidence": policy.get("language_confidence"),
        "language_source": policy.get("language_source"),
        "conversation_language": policy.get("conversation_language"),
        "reply_language": policy.get("reply_language"),
        "supported_languages": list(policy.get("supported_languages") or []),
        "language_result": policy,
    }


def make_language_policy_node(**settings):
    async def node(state: GraphState) -> GraphState:
        return language_policy_node(state, **settings)

    return node


def intent_router_node(state: GraphState) -> GraphState:
    if state.get("route_locked") and state.get("route"):
        return state
    if state.get("route_source") in LLM_AUTHORITATIVE_SOURCES and state.get("route"):
        return state
    if (state.get("llm_router_result") or {}).get("hard_guard") == "backend_fact" and state.get("route"):
        return state
    active = state.get("active_workflow")
    stage = state.get("workflow_stage")
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
    lower = text.lower()
    hints = extract_route_hints(state)

    if hints["has_explicit_human_request"]:
        return _with_route(state, "explicit_human_request", "human_handoff", "Customer explicitly requested a human agent.")
    if active and stage in ACTIVE_WORKFLOW_GUARD_STAGES:
        return _active_workflow_deterministic_route(state, str(active))
    if _is_service_frustration(lower):
        return _with_route(
            state,
            "service_frustration",
            "human_handoff",
            "Repeated service frustration should be handed to a human.",
            risk_level="elevated",
        )
    if _is_unsupported_concrete_issue(lower):
        return _with_route(
            state,
            "unsupported_concrete_issue",
            "human_handoff",
            "Technical/game-specific issues are out of FAQ/SOP scope.",
        )
    if _is_account_access_issue(lower):
        return _with_route(
            state,
            "account_access_issue",
            "human_handoff",
            "Account access problems require manual support.",
        )
    if _is_account_profile_or_wallet_change(lower):
        return _with_route(
            state,
            "account_profile_or_wallet_change",
            "human_handoff",
            "Profile or wallet changes require manual support.",
        )
    if _is_abusive_or_emotional(lower):
        return _with_route(
            state,
            "abusive_or_emotional",
            "emotion_care",
            "High-emotion language should receive a calming response first.",
            emotion="distressed",
            risk_level="high",
        )
    if _is_pending_reply_lookup(lower):
        return _with_route(state, "pending_reply_lookup", "sop", "Previous case lookup requires SOP handling.", sop_name="pending_reply_lookup")
    if _is_deposit_missing(lower):
        return _with_route(state, "deposit_missing", "sop", "Deposit-not-arrived issues require SOP handling.", sop_name="deposit_missing")
    if _is_withdrawal_missing(lower):
        return _with_route(
            state,
            "withdrawal_missing",
            "sop",
            "Withdrawal-not-arrived issues require SOP handling.",
            sop_name="withdrawal_missing",
        )
    if _is_withdrawal_blocked_or_rollover(lower):
        return _with_route(
            state,
            "withdrawal_blocked_or_rollover",
            "faq_then_sop",
            "Withdrawal restriction or rollover questions need explanation first, then SOP.",
            sop_name="withdrawal_blocked_or_rollover",
            faq_query=text,
        )
    if _is_deposit_howto(lower):
        return _with_route(state, "deposit_howto", "faq", "Deposit how-to is a FAQ/manual question.", faq_query=text)
    if _is_withdrawal_howto(lower):
        return _with_route(state, "withdrawal_howto", "faq", "Withdrawal how-to is a FAQ/manual question.", faq_query=text)
    if _is_forgot_password_howto(lower):
        return _with_route(state, "forgot_password_howto", "faq", "Forgot-password instructions are FAQ/manual content.", faq_query=text)
    if _is_screenshot_upload_howto(lower, hints):
        return _with_route(state, "screenshot_upload_howto", "faq", "Screenshot upload instructions are FAQ/manual content.", faq_query=text)
    if _is_menu_help(lower, hints):
        return _with_route(state, "clarification_needed", "clarification", "Menu recovery is outside canonical FAQ.")
    if state.get("event_type") == "FILE_RECEIVED":
        return _with_route(state, "clarification_needed", "clarification", "File upload without a clear issue needs clarification.")
    if _is_casual_chat(state):
        return _with_route(state, "casual_chat", "casual_chat", "Non-business greeting or small talk.", confidence=0.82)
    if text:
        return _with_route(state, "clarification_needed", "clarification", "Question is outside canonical FAQ targets.", confidence=0.55)
    return _with_route(state, "clarification_needed", "clarification", "No clear question content provided.", confidence=0.2)


def make_intent_router_node(
    llm_intent_service=None,
    *,
    llm_intent_min_confidence: float = 0.70,
    llm_intent_fallback_to_deterministic: bool = True,
):
    min_confidence = float(llm_intent_min_confidence)
    fallback_to_deterministic = bool(llm_intent_fallback_to_deterministic)

    async def node(state: GraphState) -> GraphState:
        if state.get("route_locked") and state.get("route"):
            return state
        raw = normalize_text(state.get("raw_user_input"))
        if is_explicit_human_request(raw):
            next_state = _with_route(
                state,
                "explicit_human_request",
                "human_handoff",
                "Customer explicitly requested a human agent.",
            )
            next_state["llm_router_result"] = _router_result_summary(
                "fallback",
                mode="guarded_authoritative",
                fallback_reason="explicit_human_request_guard",
                fallback_to_deterministic=True,
            )
            return next_state
        if state.get("event_type") == "FILE_RECEIVED" and not state.get("active_workflow") and not raw:
            return intent_router_node(state)
        if not llm_intent_service or not hasattr(llm_intent_service, "route"):
            return _router_fallback_state(state, "missing_provider", "guarded_authoritative", fallback_to_deterministic)

        payload = _build_llm_router_payload(state)
        try:
            raw_result = await llm_intent_service.route(payload)
            raw = dict(raw_result or {})
            decision = validate_router_decision_output(payload, raw)
        except Exception as exc:
            return _router_fallback_state(
                state,
                "exception" if not isinstance(exc, ValueError) else "validation_error",
                "guarded_authoritative",
                fallback_to_deterministic,
                exc=exc,
            )

        provider = raw.get("provider")
        mode = raw.get("mode") or "guarded_authoritative"
        if float(decision.get("confidence") or 0.0) < min_confidence:
            return _router_fallback_state(
                state,
                "low_confidence",
                "guarded_authoritative",
                fallback_to_deterministic,
                decision=decision,
                provider=provider,
                mode=mode,
            )
        if decision["route"] == "unsupported":
            return _router_fallback_state(
                state,
                "unsupported_route",
                "guarded_authoritative",
                fallback_to_deterministic,
                decision=decision,
                provider=provider,
                mode=mode,
            )

        intent_result = {
            "intent": decision["intent"],
            "route": decision["route"],
            "confidence": decision["confidence"],
            "reason": decision["reason"],
            "sop_name": decision.get("sop_name"),
            "faq_query": decision.get("faq_query"),
            "risk_level": decision.get("risk_level"),
            "requires_human": decision.get("requires_human"),
            "requires_backend": decision.get("requires_backend"),
            "missing_slots": list(decision.get("missing_slots") or []),
            "workflow_relation": decision.get("workflow_relation"),
            "preserve_active_workflow": decision.get("preserve_active_workflow"),
        }
        route = "clarification" if decision["route"] == "unsupported" else decision["route"]
        return {
            **state,
            "intent_result": intent_result,
            "llm_router_result": _router_result_summary("accepted", decision=decision, provider=provider, mode=mode),
            "route": route,
            "route_source": "llm_guarded_authoritative",
        }

    return node


def sop_node(state: GraphState) -> GraphState:
    if state.get("workflow_stage") in {"waiting_backend", "backend_querying", "waiting_customer_supplement"}:
        return handle_waiting_backend(state)
    return run_sop(state)


def rag_node(state: GraphState) -> GraphState:
    rag_result = answer_from_rag_context(state) if state.get("rag_context") is not None else answer_from_static_knowledge(state)
    fallback_text = rag_result["answer"]
    return {
        **state,
        "rag_result": rag_result,
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_plan": build_reply_plan(
            kind="faq_answer",
            fallback_text=fallback_text,
            must_say=[fallback_text] if fallback_text else [],
            must_not_say=["已到账", "已完成", "已退款", "保证到账", "手续费全免"],
            allowed_facts=[fallback_text] if fallback_text else [],
        ),
        "commands": [],
    }


def make_sop_node(
    llm_sop_slot_service=None,
    *,
    llm_sop_slot_enabled: bool = False,
    llm_sop_slot_min_confidence: float = 0.70,
):
    async def node(state: GraphState) -> GraphState:
        next_state = state
        if _should_run_sop_slot_extraction(state, llm_sop_slot_service, llm_sop_slot_enabled):
            next_state = await _run_sop_slot_extraction(state, llm_sop_slot_service, llm_sop_slot_min_confidence)
        return sop_node(next_state)

    return node


def make_rag_node(rag_service=None):
    async def node(state: GraphState) -> GraphState:
        next_state = state
        if rag_service and state.get("rag_context") is None:
            next_state = {**state, "rag_context": await rag_service.retrieve(state)}
        return rag_node(next_state)

    return node


def make_final_reply_node(final_reply_service=None, *, llm_final_reply_enabled: bool = False):
    async def node(state: GraphState) -> GraphState:
        if final_reply_service and hasattr(final_reply_service, "compose"):
            return await final_reply_service.compose(state)
        if llm_final_reply_enabled and state.get("response_text"):
            text = state.get("response_text_fallback") or state.get("response_text")
            return {
                **state,
                "response_text_fallback": text,
                "final_response_text": text,
                "final_reply_result": {"status": "fallback", "fallback_reason": "missing_service"},
            }
        return state

    return node


def human_handoff_node(state: GraphState) -> GraphState:
    intent = (state.get("intent_result") or {}).get("intent")
    reason = intent or "explicit_human_request"
    handoff_text = "我会为你转接真人客服继续协助。"
    return {
        **state,
        "status": "HANDOFF_REQUESTED",
        "active_workflow": "human_handoff",
        "workflow_stage": "handoff_requested",
        "response_text": handoff_text,
        "response_text_fallback": handoff_text,
        "reply_plan": build_reply_plan(
            kind="human_handoff",
            fallback_text=handoff_text,
            must_say=["转接真人客服"],
            semantic_required_items=["human_handoff_notice"],
            must_not_say=["已接入", "马上处理", "已处理", "已到账", "已完成"],
            allowed_facts=["客户需要真人客服", "系统将提出转接请求"],
        ),
        "commands": [
            {
                "type": CommandType.LIVECHAT_SEND_TEXT,
                "payload": {"text": handoff_text, "handoff_ack": True},
            },
            {
                "type": CommandType.HUMAN_HANDOFF_REQUESTED,
                "payload": {"reason": reason},
            },
        ],
    }


def emotion_care_node(state: GraphState) -> GraphState:
    return {
        **state,
        "response_text": "我理解你现在很着急。我会先尽力说明处理方式；如果你愿意，也可以直接告诉我需要转接真人客服。",
        "response_text_fallback": "我理解你现在很着急。我会先尽力说明处理方式；如果你愿意，也可以直接告诉我需要转接真人客服。",
        "reply_plan": build_reply_plan(
            kind="emotion_care",
            fallback_text="我理解你现在很着急。我会先尽力说明处理方式；如果你愿意，也可以直接告诉我需要转接真人客服。",
            must_say=["理解", "转接真人客服"],
            semantic_required_items=["human_handoff_notice"],
            must_not_say=["已到账", "已完成", "保证"],
            allowed_facts=["客户情绪较急", "可以请求转接真人客服"],
        ),
        "commands": [],
    }


def contextual_reply_node(state: GraphState) -> GraphState:
    relation = (state.get("intent_result") or {}).get("workflow_relation")
    if relation == "contextual_followup":
        return _contextual_followup_state(state)
    return _acknowledgement_state(state)


def casual_chat_node(state: GraphState) -> GraphState:
    fallback_text = _casual_reply_text(str(state.get("reply_language") or state.get("conversation_language") or "zh-Hans"))
    return {
        **state,
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_plan": build_reply_plan(
            kind="casual_chat",
            fallback_text=fallback_text,
            must_not_say=["已到账", "已完成", "已处理", "已同步", "tg:"],
            allowed_facts=["用户发送非业务闲聊或问候"],
        ),
        "commands": [],
    }


def clarification_node(state: GraphState) -> GraphState:
    fallback_text = "请补充你要咨询的问题，或说明是存款、提款、流水还是需要真人客服。"
    return {
        **state,
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_plan": build_reply_plan(
            kind="clarification",
            fallback_text=fallback_text,
            must_say=["存款", "提款", "流水", "真人客服"],
            must_not_say=["已到账", "已完成", "已处理"],
            allowed_facts=["需要客户补充问题类型"],
        ),
        "commands": [],
    }


def command_planner_node(state: GraphState) -> GraphState:
    commands = list(state.get("commands") or [])
    text = state.get("final_response_text") or state.get("response_text")
    if text:
        for command in commands:
            if str(command.get("type")) == str(CommandType.LIVECHAT_SEND_TEXT):
                command["payload"] = {**dict(command.get("payload") or {}), "text": text}
                break
        else:
            commands.insert(
                0,
                {
                    "type": CommandType.LIVECHAT_SEND_TEXT,
                    "payload": {"text": text},
                },
            )
    return {**state, "commands": commands}


def _acknowledgement_state(state: GraphState) -> GraphState:
    fallback_text = _acknowledgement_reply_text(state)
    return {
        **state,
        "sop_action": "acknowledgement",
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_plan": build_reply_plan(
            kind="acknowledgement",
            fallback_text=fallback_text,
            must_not_say=["已到账", "已完成", "已处理", "已同步", "已补充给后台", "tg:"],
            allowed_facts=[fallback_text],
            metadata={"workflow_relation": "acknowledgement"},
        ),
        "commands": [],
    }


def _contextual_followup_state(state: GraphState) -> GraphState:
    fallback_text = _contextual_followup_reply_text(state)
    return {
        **state,
        "sop_action": "contextual_followup",
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_plan": build_reply_plan(
            kind="contextual_followup",
            fallback_text=fallback_text,
            must_not_say=["已到账", "已完成", "已处理", "已同步", "已补充给后台", "tg:"],
            allowed_facts=[fallback_text],
            metadata={"workflow_relation": "contextual_followup"},
        ),
        "commands": [],
    }


def persist_state_node(state: GraphState) -> GraphState:
    return state


def _intent_result(
    intent: str,
    route: str,
    reason: str,
    confidence: float = 0.9,
    sop_name: str | None = None,
    faq_query: str | None = None,
    emotion: str | None = None,
    risk_level: str | None = None,
    workflow_relation: str | None = None,
    preserve_active_workflow: bool | None = None,
) -> dict[str, Any]:
    result = {
        "intent": intent,
        "route": route,
        "confidence": confidence,
        "reason": reason,
    }
    if sop_name:
        result["sop_name"] = sop_name
    if faq_query:
        result["faq_query"] = faq_query
    if emotion:
        result["emotion"] = emotion
    if risk_level:
        result["risk_level"] = risk_level
    if workflow_relation:
        result["workflow_relation"] = workflow_relation
    if preserve_active_workflow is not None:
        result["preserve_active_workflow"] = preserve_active_workflow
    return result


def _with_route(
    state: GraphState,
    intent: str,
    route: str,
    reason: str,
    confidence: float = 0.9,
    sop_name: str | None = None,
    faq_query: str | None = None,
    emotion: str | None = None,
    risk_level: str | None = None,
    workflow_relation: str | None = None,
    preserve_active_workflow: bool | None = None,
) -> GraphState:
    return {
        **state,
        "intent_result": _intent_result(
            intent=intent,
            route=route,
            reason=reason,
            confidence=confidence,
            sop_name=sop_name,
            faq_query=faq_query,
            emotion=emotion,
            risk_level=risk_level,
            workflow_relation=workflow_relation,
            preserve_active_workflow=preserve_active_workflow,
        ),
        "route": route,
    }


def _active_workflow_deterministic_route(state: GraphState, active_workflow: str) -> GraphState:
    if _is_acknowledgement(state):
        return _with_route(
            state,
            "acknowledgement",
            "contextual_reply",
            "Customer acknowledged the previous message; preserve active workflow without SOP side effects.",
            confidence=0.88,
            workflow_relation="acknowledgement",
            preserve_active_workflow=True,
        )
    if _is_contextual_followup(state):
        return _with_route(
            state,
            "contextual_followup",
            "contextual_reply",
            "Customer asks a context-dependent follow-up about the active workflow requirements.",
            confidence=0.86,
            workflow_relation="contextual_followup",
            preserve_active_workflow=True,
        )
    faq = _is_independent_faq_during_workflow(state)
    if faq:
        return _with_route(
            state,
            faq["intent"],
            "faq",
            "Independent FAQ during active workflow; preserve current SOP state.",
            confidence=0.86,
            faq_query=faq["faq_query"],
            workflow_relation="independent_faq",
            preserve_active_workflow=True,
        )
    if _is_cross_workflow_business_object(state, active_workflow):
        return _with_route(
            state,
            "clarification_needed",
            "clarification",
            "Message mentions a different business object than the active workflow; ask before changing state.",
            confidence=0.78,
            workflow_relation="new_workflow_request",
            preserve_active_workflow=True,
        )
    if _is_new_workflow_request(state, active_workflow):
        return _with_route(
            state,
            "clarification_needed",
            "clarification",
            "New SOP-like request during active workflow; ask whether to switch workflow before changing state.",
            confidence=0.78,
            workflow_relation="new_workflow_request",
            preserve_active_workflow=True,
        )
    if _is_current_workflow_supplement(state, active_workflow):
        return _with_route(
            state,
            active_workflow,
            "sop",
            "Message appears to supplement the active SOP workflow.",
            confidence=0.86,
            sop_name=active_workflow,
            workflow_relation="current_workflow_supplement",
            preserve_active_workflow=True,
        )
    return _with_route(
        state,
        "clarification_needed",
        "clarification",
        "Message relation to the active workflow is unclear.",
        confidence=0.55,
        workflow_relation="unclear",
        preserve_active_workflow=True,
    )


def _is_acknowledgement(state: GraphState) -> bool:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    normalized = re.sub(r"[.!?。！？…\s]+", "", text)
    return normalized in {
        "ok",
        "okay",
        "好的",
        "好",
        "谢谢",
        "謝謝",
        "明白",
        "了解",
        "收到",
        "知道了",
        "thanks",
        "thankyou",
        "gracias",
        "vale",
        "bueno",
        "listo",
    }


def _is_contextual_followup(state: GraphState) -> bool:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    if not text:
        return False
    name_patterns = (
        r"\bmay i provide my name\b",
        r"\bcan i provide my name\b",
        r"\bcan i give (?:you )?my name\b",
        r"\bwould my name\b",
        r"\bname (?:instead|enough|ok|okay)\b",
        r"可以.*(姓名|名字)",
        r"(姓名|名字).*可以",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in name_patterns)


def _is_casual_chat(state: GraphState) -> bool:
    if state.get("active_workflow") or state.get("workflow_stage"):
        return False
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    normalized = re.sub(r"[,，.!?。！？…\s]+", " ", text).strip()
    if _is_acknowledgement(state):
        return True
    return bool(
        re.fullmatch(
            r"(hi|hello|hey|hola|你好|您好|how are you|hello how are you|hi how are you|你好吗|在吗|在嗎)",
            normalized,
            flags=re.I,
        )
    )


def _acknowledgement_reply_text(state: GraphState) -> str:
    language = str(state.get("reply_language") or state.get("conversation_language") or "zh-Hans").lower()
    stage = str(state.get("workflow_stage") or "")
    if language.startswith("en"):
        if stage in {"waiting_backend", "backend_querying"}:
            return "Got it. The case is still being checked, and I will update you here once there is progress."
        if stage == "waiting_customer_supplement":
            return "Got it. Please send the requested details here when you have them, and I will continue helping you check this."
        return "Got it. You can send the requested details here whenever you are ready."
    if stage in {"waiting_backend", "backend_querying"}:
        return "收到，案件仍在确认中，有更新会在这里通知你。"
    if stage == "waiting_customer_supplement":
        return "收到，请你确认后把需要补充的资料发给我，我会继续协助核实。"
    return "收到，你准备好后可以继续把需要补充的资料发给我。"


def _contextual_followup_reply_text(state: GraphState) -> str:
    language = str(state.get("reply_language") or state.get("conversation_language") or "zh-Hans").lower()
    active = str(state.get("active_workflow") or "")
    if language.startswith("en"):
        if active == "withdrawal_missing":
            return (
                "Yes, you may provide your name, but for checking this withdrawal case we still need your registered "
                "phone number and a screenshot of the withdrawal request or receipt. Your name alone may not be enough "
                "to locate the record."
            )
        return "Yes, you may provide your name, but we may still need the requested account details and screenshot to continue checking."
    if active == "withdrawal_missing":
        return "可以提供姓名，但为了查询这笔提款，我们仍需要你的注册手机号和提款截图或凭证。只有姓名可能无法准确核实记录。"
    return "可以提供姓名，但为了继续核实，我们可能仍需要你按前面要求提供账号资料和截图。"


def _casual_reply_text(language: str) -> str:
    if language.lower().startswith("en"):
        return "Hello, I am here to help. Please tell me what you need assistance with."
    return "你好，我在这里协助你。请告诉我需要咨询的问题。"


def _build_llm_rewrite_payload(state: GraphState) -> dict[str, Any]:
    return {
        "tenant_id": state.get("tenant_id"),
        "conversation_id": state.get("conversation_id"),
        "channel_type": state.get("channel_type"),
        "raw_user_input": state.get("raw_user_input"),
        "event_type": state.get("event_type"),
        "attachments": list(state.get("attachments") or []),
        "attachments_summary": _attachments_summary(state),
        "recent_messages": list(state.get("recent_messages") or []),
        "active_workflow": state.get("active_workflow"),
        "workflow_stage": state.get("workflow_stage"),
        "slot_memory": dict(state.get("slot_memory") or {}),
        "current_rewritten_question": state.get("rewritten_question"),
        "deterministic_rewrite_result": state.get("rewrite_result"),
    }


def _deterministic_rewrite_fallback(
    state: GraphState,
    fallback_reason: str,
    *,
    result: dict | None = None,
    exc: Exception | None = None,
) -> GraphState:
    fallback = rewrite_question_node(state)
    fallback["llm_rewrite_result"] = {
        "status": "fallback",
        "fallback_reason": fallback_reason,
        "confidence": (result or {}).get("confidence"),
        "error_type": type(exc).__name__ if exc else None,
        "error_message": _redact_sensitive_text(str(exc)[:1000]) if exc else None,
    }
    fallback["rewrite_source"] = "deterministic_fallback"
    return fallback


def _build_llm_router_payload(state: GraphState) -> dict[str, Any]:
    return {
        "tenant_id": state.get("tenant_id"),
        "conversation_id": state.get("conversation_id"),
        "raw_user_input": state.get("raw_user_input"),
        "rewritten_question": state.get("rewritten_question"),
        "reply_language": state.get("reply_language"),
        "recent_messages": list(state.get("recent_messages") or []),
        "slot_memory": dict(state.get("slot_memory") or {}),
        "active_workflow": state.get("active_workflow"),
        "workflow_stage": state.get("workflow_stage"),
        "attachments_summary": _attachments_summary(state),
        "deterministic_intent_result": None,
        "deterministic_route": None,
    }


def _router_fallback_state(
    state: GraphState,
    fallback_reason: str,
    router_mode: str,
    fallback_to_deterministic: bool,
    *,
    decision: dict | None = None,
    provider: str | None = None,
    mode: str | None = None,
    exc: Exception | None = None,
) -> GraphState:
    if fallback_to_deterministic:
        next_state = intent_router_node(state)
    else:
        next_state = {
            **state,
            "intent_result": {
                "intent": "clarification_needed",
                "route": "clarification",
                "confidence": 0.0,
                "reason": "LLM intent classifier failed and deterministic fallback is disabled.",
            },
            "route": "clarification",
            "route_source": "llm_guarded_authoritative",
        }
    next_state["llm_router_result"] = _router_result_summary(
        "fallback",
        decision=decision,
        provider=provider,
        mode=mode or router_mode,
        fallback_reason=fallback_reason,
        error_type=type(exc).__name__ if exc else None,
        error_message=_redact_sensitive_text(str(exc)[:1000]) if exc else None,
        fallback_to_deterministic=fallback_to_deterministic,
    )
    return next_state


def _router_result_summary(
    status: str,
    decision: dict | None = None,
    provider: str | None = None,
    mode: str | None = None,
    fallback_reason: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    fallback_to_deterministic: bool | None = None,
) -> dict[str, Any]:
    decision = decision or {}
    summary = {
        "provider": provider or decision.get("provider"),
        "mode": mode or decision.get("mode") or "guarded_authoritative",
        "status": status,
        "intent": decision.get("intent"),
        "route": decision.get("route"),
        "confidence": decision.get("confidence"),
        "reason": decision.get("reason"),
        "sop_name": decision.get("sop_name"),
        "faq_query": decision.get("faq_query"),
        "requires_human": decision.get("requires_human"),
        "requires_backend": decision.get("requires_backend"),
        "missing_slots": decision.get("missing_slots"),
        "workflow_relation": decision.get("workflow_relation"),
        "preserve_active_workflow": decision.get("preserve_active_workflow"),
    }
    if fallback_reason:
        summary["fallback_reason"] = fallback_reason
    if error_type:
        summary["error_type"] = error_type
    if error_message:
        summary["error_message"] = error_message
    if fallback_to_deterministic is not None:
        summary["fallback_to_deterministic"] = fallback_to_deterministic
    return {key: value for key, value in summary.items() if value is not None}


def _is_current_workflow_supplement(state: GraphState, active_workflow: str) -> bool:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
    lower = text.lower()
    if state.get("attachments") and active_workflow in {"deposit_missing", "withdrawal_missing"}:
        return True
    if extract_amount(text) or extract_identity(text) or extract_order_id(text):
        return True
    if len(lower) <= 32 and any(char.isdigit() for char in lower):
        return True
    current_tokens = {
        "deposit_missing": (
            "截图",
            "凭证",
            "已付",
            "已转",
            "支付",
            "付款",
            "充值",
            "存款",
            "到账",
            "没到账",
            "未到账",
            "deposit",
            "paid",
            "sent",
            "submitted",
            "mandé",
            "mande",
            "enviado",
            "payment",
            "receipt",
            "proof",
            "depósito",
            "deposito",
        ),
        "withdrawal_missing": (
            "截图",
            "凭证",
            "提款",
            "提现",
            "到账",
            "没到账",
            "未到账",
            "withdraw",
            "withdrawal",
            "retiro",
            "sent",
            "submitted",
            "mandé",
            "mande",
            "enviado",
            "proof",
        ),
        "withdrawal_blocked_or_rollover": ("流水", "rollover", "提款", "提现", "withdraw", "限制", "blocked"),
        "pending_reply_lookup": ("之前", "上一笔", "案件", "回复", "case", "ticket", "previous"),
    }
    return _contains_any(lower, current_tokens.get(active_workflow, ()))


def _is_independent_faq_during_workflow(state: GraphState) -> dict[str, str] | None:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    if _is_deposit_howto(text) or _contains_any(text, ("怎么存款", "如何存款", "怎么充值")):
        return {"intent": "deposit_howto", "faq_query": "怎么存款"}
    if _is_withdrawal_howto(text) or _contains_any(text, ("怎么提款", "怎么提现", "如何提现")):
        return {"intent": "withdrawal_howto", "faq_query": "如何提款"}
    if _is_forgot_password_howto(text):
        return {"intent": "forgot_password_howto", "faq_query": "忘记密码"}
    if _is_screenshot_upload_howto(text, extract_route_hints(state)) or _contains_any(text, ("怎么上传截图", "如何上传截图")):
        return {"intent": "screenshot_upload_howto", "faq_query": "上传截图"}
    if _is_rollover_explanation(text) or _contains_any(text, ("流水是什么意思", "流水是什么", "rollover 是什么")):
        return {"intent": "rollover_explanation", "faq_query": "流水说明"}
    return None


def _is_new_workflow_request(state: GraphState, active_workflow: str) -> bool:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    candidate: str | None = None
    if _is_deposit_missing(text):
        candidate = "deposit_missing"
    elif _is_withdrawal_missing(text):
        candidate = "withdrawal_missing"
    elif _is_withdrawal_blocked_or_rollover(text):
        candidate = "withdrawal_blocked_or_rollover"
    elif _is_pending_reply_lookup(text):
        candidate = "pending_reply_lookup"
    elif _is_account_access_issue(text) or _is_account_profile_or_wallet_change(text):
        candidate = "manual_support_workflow"
    return bool(candidate and candidate != active_workflow)


def _is_cross_workflow_business_object(state: GraphState, active_workflow: str) -> bool:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    mentions_deposit = _contains_any(text, ("deposit", "depósito", "deposito", "存款", "充值"))
    mentions_withdrawal = _contains_any(text, ("withdraw", "withdrawal", "retiro", "提款", "提现"))
    if active_workflow == "withdrawal_missing" and mentions_deposit and not mentions_withdrawal:
        return True
    if active_workflow == "deposit_missing" and mentions_withdrawal and not mentions_deposit:
        return True
    return False


def _should_run_sop_slot_extraction(state: GraphState, service, enabled: bool) -> bool:
    intent = (state.get("intent_result") or {}).get("intent")
    return bool(
        enabled
        and service
        and (hasattr(service, "plan_sop_dialogue") or hasattr(service, "extract_sop_slots"))
        and state.get("route") == "sop"
        and intent in {"deposit_missing", "withdrawal_missing"}
        and state.get("workflow_stage") not in {"waiting_backend", "backend_querying"}
    )


async def _run_sop_slot_extraction(state: GraphState, service, min_confidence: float) -> GraphState:
    if hasattr(service, "plan_sop_dialogue"):
        planned_state = await _run_sop_dialogue_planning(state, service, min_confidence)
        if planned_state.get("sop_slot_source") == "llm_dialogue_planner":
            return planned_state
        if hasattr(service, "extract_sop_slots"):
            return await _run_legacy_sop_slot_extraction(planned_state, service, min_confidence)
        return planned_state
    return await _run_legacy_sop_slot_extraction(state, service, min_confidence)


async def _run_sop_dialogue_planning(state: GraphState, service, min_confidence: float) -> GraphState:
    intent = (state.get("intent_result") or {}).get("intent")
    definition = get_sop_definition(intent)
    if definition is None:
        return _sop_slot_fallback_state(state, "unsupported_sop")
    payload = build_llm_sop_dialogue_input(state, str(intent), definition)
    try:
        raw_result = await service.plan_sop_dialogue(payload)
        result = validate_sop_dialogue_planner_output(payload, dict(raw_result or {}))
        if _sop_dialogue_low_confidence(result, min_confidence):
            return _sop_slot_fallback_state(
                state,
                "low_confidence",
                result=result,
                dialogue_plan=_sop_dialogue_plan_summary("fallback", raw_result, result, fallback_reason="low_confidence"),
            )
        slot_memory = dict(state.get("slot_memory") or {})
        for key, value in (result.get("slot_updates") or {}).items():
            if value:
                slot_memory[key] = value
        plan_summary = _sop_dialogue_plan_summary("accepted", raw_result, result)
        return {
            **state,
            "slot_memory": slot_memory,
            "llm_sop_dialogue_plan": plan_summary,
            "llm_sop_slot_result": {
                "status": "accepted",
                "provider": (raw_result or {}).get("provider"),
                "mode": (raw_result or {}).get("mode") or "sop_dialogue_planner",
                "intent": intent,
                "missing_slots": result.get("missing_slots"),
                "confidence": result.get("slot_confidence"),
                "reason": result.get("reason"),
                "dropped_slots": result.get("dropped_slots"),
            },
            "sop_slot_source": "llm_dialogue_planner",
        }
    except Exception as exc:
        fallback_reason = "validation_error" if isinstance(exc, ValueError) else "exception"
        return _sop_slot_fallback_state(
            state,
            fallback_reason,
            exc=exc,
            dialogue_plan=_sop_dialogue_plan_summary("fallback", None, None, fallback_reason=fallback_reason, exc=exc),
        )


async def _run_legacy_sop_slot_extraction(state: GraphState, service, min_confidence: float) -> GraphState:
    payload = {
        "intent": (state.get("intent_result") or {}).get("intent"),
        "current_slot_memory": dict(state.get("slot_memory") or {}),
        "latest_user_text": normalize_text(state.get("rewritten_question") or state.get("raw_user_input")),
        "attachments_summary": _attachments_summary(state),
        "recent_messages": list(state.get("recent_messages") or []),
        "language": state.get("reply_language") or "unknown",
    }
    try:
        raw_result = await service.extract_sop_slots(payload)
        result = validate_sop_slot_extraction_output(payload, dict(raw_result or {}))
        if result.get("dropped_fields") or _sop_slot_low_confidence(result, min_confidence):
            return _sop_slot_fallback_state(state, "guardrail_or_low_confidence", result=result)
        slot_memory = dict(state.get("slot_memory") or {})
        for key, value in (result.get("extracted_slots") or {}).items():
            if value:
                slot_memory[key] = value
        return {
            **state,
            "slot_memory": slot_memory,
            "llm_sop_slot_result": {
                "status": "accepted",
                "provider": (raw_result or {}).get("provider"),
                "mode": (raw_result or {}).get("mode") or "sop_slot",
                "intent": result.get("intent"),
                "missing_slots": result.get("missing_slots"),
                "confidence": result.get("confidence"),
                "reason": result.get("reason"),
            },
            "sop_slot_source": "llm_guarded",
        }
    except Exception as exc:
        return _sop_slot_fallback_state(state, "exception", exc=exc)


def _sop_slot_low_confidence(result: dict, min_confidence: float) -> bool:
    extracted = result.get("extracted_slots") or {}
    confidence = result.get("confidence") or {}
    for key, value in extracted.items():
        if value and float(confidence.get(key) or 0.0) < float(min_confidence):
            return True
    return False


def _sop_dialogue_low_confidence(result: dict, min_confidence: float) -> bool:
    updates = result.get("slot_updates") or {}
    confidence = result.get("slot_confidence") or {}
    for key, value in updates.items():
        if value and float(confidence.get(key) or 0.0) < float(min_confidence):
            return True
    return False


def _sop_dialogue_plan_summary(
    status: str,
    raw_result: dict | None,
    result: dict | None,
    *,
    fallback_reason: str | None = None,
    exc: Exception | None = None,
) -> dict[str, Any]:
    raw_result = raw_result or {}
    result = result or {}
    summary = {
        "status": status,
        "provider": raw_result.get("provider"),
        "mode": raw_result.get("mode") or "sop_dialogue_planner",
        "intent_relation": result.get("intent_relation"),
        "extracted_slots": result.get("extracted_slots"),
        "slot_updates": result.get("slot_updates"),
        "slot_confidence": result.get("slot_confidence"),
        "missing_slots": result.get("missing_slots"),
        "should_ask_confirmation": result.get("should_ask_confirmation"),
        "reply_draft": result.get("reply_draft"),
        "reason": result.get("reason"),
        "dropped_slots": result.get("dropped_slots"),
        "fallback_reason": fallback_reason,
        "error_type": type(exc).__name__ if exc else None,
        "error_message": _redact_sensitive_text(str(exc)[:1000]) if exc else None,
    }
    return {key: value for key, value in summary.items() if value is not None}


def _sop_slot_fallback_state(
    state: GraphState,
    fallback_reason: str,
    result: dict | None = None,
    exc: Exception | None = None,
    dialogue_plan: dict | None = None,
) -> GraphState:
    next_state = {
        **state,
        "llm_sop_slot_result": {
            "status": "fallback",
            "fallback_reason": fallback_reason,
            "intent": (result or {}).get("intent"),
            "error_type": type(exc).__name__ if exc else None,
            "error_message": _redact_sensitive_text(str(exc)[:1000]) if exc else None,
        },
        "sop_slot_source": "deterministic",
    }
    if dialogue_plan is not None:
        next_state["llm_sop_dialogue_plan"] = dialogue_plan
    return next_state


def _attachments_summary(state: GraphState) -> list[dict[str, Any]]:
    return [{"url": item.get("url"), "name": item.get("name")} for item in state.get("attachments") or []]


def _default_reply_language(tenant_default: str, fallback: str, supported: list[str]) -> str:
    for candidate in (tenant_default, fallback):
        normalized = normalize_language_code(candidate)
        if normalized != "unknown" and normalized in supported:
            return normalized
    return supported[0] if supported else "zh-Hans"


def _redact_sensitive_text(value: str) -> str:
    redacted = re.sub(
        r"\b(Authorization)\s*[:=]\s*Bearer\s+['\"]?([^'\"\s,;]+)['\"]?",
        lambda match: f"{match.group(1)}: Bearer [redacted]",
        value,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"\b(access[-_]?token|x-api-key|api[-_]?key|secret|password|token)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?",
        lambda match: f"{match.group(1)}=[redacted]",
        redacted,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"\b(Bearer)\s+['\"]?([^'\"\s,;]+)['\"]?",
        lambda match: f"{match.group(1)} [redacted]",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def _has_waiting_supplement(signal: dict[str, Any], state: GraphState) -> bool:
    return bool(state.get("attachments") or signal.get("has_contact_hint"))


def extract_route_hints(state: GraphState) -> dict[str, Any]:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
    lower = text.lower()
    return {
        "has_explicit_human_request": is_explicit_human_request(text),
        "has_menu_signal": any(token in lower for token in ("menu", "menú")),
        "has_screenshot_signal": any(token in lower for token in ("screenshot", "captura", "截图")),
        "has_attachment": bool(attachment_urls(state.get("attachments", []))),
        "has_contact_hint": extract_identity(text) is not None,
    }


def _extract_text(payload: dict[str, Any]) -> str:
    event = payload.get("event") or {}
    return normalize_text(event.get("text") or payload.get("text") or payload.get("message"))


def _extract_attachments(payload: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    attachments = list(payload.get("attachments") or [])
    event = payload.get("event") or {}
    if event_type == "FILE_RECEIVED":
        file_payload = event.get("file") if isinstance(event.get("file"), dict) else event
        url = file_payload.get("url") or file_payload.get("content_url") or file_payload.get("thumbnail_url")
        if url:
            attachments.append({"url": url, "name": file_payload.get("name") or file_payload.get("filename")})
    return attachments


def _detect_language(text: str) -> str:
    return detect_language_deterministic(text)["detected_language"]


def _is_deposit_missing(text: str) -> bool:
    return _contains_any(text, ("deposit", "depósito", "deposito", "存款", "充值")) and _contains_any(
        text, ("no llegó", "no llego", "未到账", "没到账", "no acreditado", "nunca me pagaron")
    )


def _is_withdrawal_missing(text: str) -> bool:
    return _contains_any(text, ("retiro", "withdrawal", "提款", "提现")) and _contains_any(
        text, ("no llegó", "no llego", "未到账", "没到账", "no acreditado", "nunca me pagaron")
    )


def _is_withdrawal_blocked_or_rollover(text: str) -> bool:
    return _contains_any(text, ("no puedo retirar", "无法提款", "withdrawal blocked", "rollover", "流水"))


def _is_pending_reply_lookup(text: str) -> bool:
    return _contains_any(text, ("caso anterior", "previous case", "上一笔案件"))


def _is_deposit_howto(text: str) -> bool:
    return _contains_any(text, ("cómo recargar", "como recargar", "how to deposit", "如何充值", "充值方式"))


def _is_withdrawal_howto(text: str) -> bool:
    return _contains_any(text, ("cómo puedo retirar", "como puedo retirar", "cómo retirar", "como retirar", "how to withdraw", "如何提款"))


def _is_forgot_password_howto(text: str) -> bool:
    return _contains_any(text, ("forgot password", "olvidé mi contraseña", "olvide mi contraseña", "忘记密码"))


def _is_screenshot_upload_howto(text: str, hints: dict[str, Any]) -> bool:
    return hints.get("has_screenshot_signal") and _contains_any(text, ("subir", "upload", "enviar", "上传"))


def _is_rollover_explanation(text: str) -> bool:
    return _contains_any(text, ("qué es rollover", "que es rollover", "what is rollover", "流水是什么", "rollover explanation"))


def _is_menu_help(text: str, hints: dict[str, Any]) -> bool:
    return hints.get("has_menu_signal") and _contains_any(text, ("no veo", "ningun", "ningún", "dónde", "donde", "where", "找不到"))


def _is_service_frustration(text: str) -> bool:
    return _contains_any(text, ("todo el tiempo lo mismo", "siempre lo mismo", "otra vez lo mismo", "same thing every time"))


def _is_unsupported_concrete_issue(text: str) -> bool:
    return _contains_any(text, ("problemas técnicos", "problemas tecnicos", "technical issue", "del juego", "game issue"))


def _is_account_access_issue(text: str) -> bool:
    return _contains_any(text, ("no puedo entrar", "no puedo iniciar sesión", "no puedo iniciar sesion", "can't log in", "无法登录"))


def _is_account_profile_or_wallet_change(text: str) -> bool:
    return _contains_any(text, ("cambiar wallet", "change wallet", "cambiar perfil", "change profile", "cambiar correo", "cambiar telefono"))


def _is_abusive_or_emotional(text: str) -> bool:
    return _contains_any(text, ("basura", "mierda", "estafa", "scam", "骗子", "垃圾"))


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)
