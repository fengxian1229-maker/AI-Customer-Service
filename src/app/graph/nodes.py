from typing import Any
import re

from app.graph.state import GraphState
from app.llm.guardrails import validate_router_decision_output, validate_sop_dialogue_planner_output, validate_sop_slot_extraction_output
from app.schemas.events import InboundEvent
from app.workflows.command_contracts import CommandType
from app.services.faq_delivery import LIVECHAT_BUTTON_SOURCE, prepare_faq_context_for_delivery
from app.services.rag import answer_from_rag_context, answer_from_static_knowledge
from app.services.livechat_menus import BUSINESS_BUTTON_ROUTES, MENU_BY_NAV_BUTTON, detect_button_id
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
from app.workflows.waiting_backend_classifier import handle_waiting_backend, has_workflow_resolution_signal
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.llm_sop_dialogue_planner import build_llm_sop_dialogue_input
from app.workflows.sop_definitions import get_sop_definition


LLM_AUTHORITATIVE_SOURCES = {
    "llm_guarded_authoritative",
    "llm_guarded_authoritative_post_guard",
    "llm_rewrite_authoritative",
}

ACTIVE_WORKFLOW_GUARD_STAGES = {
    "waiting_backend",
    "backend_querying",
    "backend_replied",
    "collecting_slots",
    "lookup_pending_reply",
}


def build_graph_state_from_event(
    event: InboundEvent,
    conversation: dict[str, Any],
    recent_messages: list[dict[str, Any]] | None = None,
    previous_thread_memory: list[dict[str, Any]] | None = None,
) -> GraphState:
    payload = event.payload_json or {}
    raw_input = _extract_text(payload)
    attachments = _extract_attachments(payload, event.standard_event_type)
    image_analysis = _extract_image_analysis(payload, attachments)
    active_workflow = conversation.get("active_workflow")
    slot_memory = dict(conversation.get("slot_memory") or {})
    if payload.get("platform"):
        slot_memory["platform"] = payload.get("platform")
    if payload.get("livechat_group_id") is not None:
        slot_memory["livechat_group_id"] = payload.get("livechat_group_id")
    image_candidate_only = bool(
        event.standard_event_type == "FILE_RECEIVED"
        and not raw_input
        and not active_workflow
        and image_analysis
    )
    return {
        "tenant_id": conversation.get("tenant_id") or event.organization_id or "default",
        "channel_type": conversation.get("channel_type") or "livechat",
        "conversation_id": conversation.get("conversation_id") or f"livechat:{event.chat_id or 'unknown'}",
        "chat_id": event.chat_id or "unknown",
        "thread_id": event.thread_id,
        "payload_json": payload,
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
        "attachments": attachments,
        "image_analysis": image_analysis,
        "image_candidate_only": image_candidate_only,
        "pending_image_confirmation": image_analysis if image_candidate_only else None,
        "verified_receipt_attachments": [],
        "status": conversation.get("status") or "AI_ACTIVE",
        "active_workflow": active_workflow,
        "workflow_stage": conversation.get("workflow_stage"),
        "slot_memory": slot_memory,
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
        "node_reply_template": None,
        "node_facts": None,
        "recent_messages": recent_messages or [],
        "previous_thread_memory": previous_thread_memory or [],
        "reply_plan": None,
        "customer_reply": None,
        "response_text_fallback": None,
        "final_response_text": None,
        "final_reply_result": None,
        "response_text": None,
        "commands": [],
        "errors": [],
    }


def rewrite_question_node(state: GraphState) -> GraphState:
    menu_state = _apply_menu_button_route(state)
    if menu_state is not None:
        return menu_state
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
        menu_state = _apply_menu_button_route(state)
        if menu_state is not None:
            return menu_state
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
    tenant_persona_default_language: str = "es",
    tenant_supported_languages: str | list[str] = "zh-Hans,zh-Hant,en,es,tl,th,my,ms",
    language_fallback: str = "es",
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
    promoted_image = _promote_pending_image_candidate_route(state, text)
    if promoted_image:
        return promoted_image
    image_candidate = _image_candidate_only_route(state)
    if image_candidate:
        return image_candidate
    auto_handoff = _auto_handoff_route(state, lower, hints)
    if auto_handoff:
        return auto_handoff
    if _is_conversation_memory_lookup(state):
        return _final_reply_route(
            state,
            "conversation_memory_lookup",
            "Customer asks to recall current conversation context.",
            confidence=0.9,
            workflow_relation="contextual_followup",
            preserve_active_workflow=bool(active),
            kind="contextual_followup",
        )
    if active and stage in ACTIVE_WORKFLOW_GUARD_STAGES:
        return _active_workflow_deterministic_route(state, str(active))
    emotion_info = _emotion_context(lower)
    if _is_pending_reply_lookup(lower):
        return _with_route(
            state,
            "pending_reply_lookup",
            "sop",
            "Previous case lookup requires SOP handling.",
            sop_name="pending_reply_lookup",
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_deposit_missing(lower):
        return _with_route(
            state,
            "deposit_missing",
            "sop",
            "Deposit-not-arrived issues require SOP handling.",
            sop_name="deposit_missing",
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_withdrawal_missing(lower):
        return _with_route(
            state,
            "withdrawal_missing",
            "sop",
            "Withdrawal-not-arrived issues require SOP handling.",
            sop_name="withdrawal_missing",
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_withdrawal_blocked_or_rollover(lower):
        return _with_route(
            state,
            "withdrawal_blocked_or_rollover",
            "sop",
            "Withdrawal restriction or rollover questions require SOP handling.",
            sop_name="withdrawal_blocked_or_rollover",
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_deposit_howto(lower):
        return _with_route(
            state,
            "deposit_howto",
            "faq",
            "Deposit how-to is a FAQ/manual question.",
            faq_query=text,
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_withdrawal_howto(lower):
        return _with_route(
            state,
            "withdrawal_howto",
            "faq",
            "Withdrawal how-to is a FAQ/manual question.",
            faq_query=text,
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_forgot_password_howto(lower):
        return _with_route(
            state,
            "forgot_password_howto",
            "faq",
            "Forgot-password instructions are FAQ/manual content.",
            faq_query=text,
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_screenshot_upload_howto(lower, hints):
        return _with_route(
            state,
            "screenshot_upload_howto",
            "faq",
            "Screenshot upload instructions are FAQ/manual content.",
            faq_query=text,
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_account_access_issue(lower):
        return _with_route(
            state,
            "account_access_issue",
            "human_handoff",
            "Account access problems require manual support.",
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_account_profile_or_wallet_change(lower):
        return _with_route(
            state,
            "account_profile_or_wallet_change",
            "human_handoff",
            "Profile or wallet changes require manual support.",
            emotion=emotion_info["emotion"],
            risk_level=emotion_info["risk_level"],
        )
    if _is_service_frustration(lower):
        return _with_route(
            state,
            "service_frustration",
            "emotion_care",
            "Repeated service frustration is the primary issue.",
            emotion="frustrated",
            risk_level="elevated",
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
    if _is_menu_help(lower, hints):
        menu_state = _increment_slot_counter(state, "menu_stuck_count")
        if _slot_counter(menu_state, "menu_stuck_count") >= 2:
            return _with_route(
                menu_state,
                "menu_stuck_repeated",
                "human_handoff",
                "Customer cannot see or use the menu after repeated attempts.",
                confidence=0.88,
            )
        return _final_reply_route(menu_state, "clarification_needed", "Menu recovery is outside canonical FAQ.", kind="clarification")
    if state.get("event_type") == "FILE_RECEIVED":
        return _final_reply_route(state, "clarification_needed", "File upload without a clear issue needs clarification.", kind="clarification")
    if _is_casual_chat(state):
        return _final_reply_route(state, "casual_chat", "Non-business greeting or small talk.", confidence=0.82, kind="casual_chat")
    if _is_conversation_memory_lookup(state):
        return _final_reply_route(
            state,
            "conversation_memory_lookup",
            "Customer asks about recent conversation content.",
            confidence=0.86,
            kind="contextual_followup",
        )
    if text:
        return _final_reply_route(state, "clarification_needed", "Question is outside canonical FAQ targets.", confidence=0.55, kind="clarification")
    return _final_reply_route(state, "clarification_needed", "No clear question content provided.", confidence=0.2, kind="clarification")


def make_intent_router_node(
    llm_intent_service=None,
    *,
    llm_intent_min_confidence: float = 0.70,
    llm_intent_fallback_to_deterministic: bool = True,
    llm_router_mode: str = "guarded_authoritative",
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
        if _is_conversation_memory_lookup(state):
            next_state = _final_reply_route(
                state,
                "conversation_memory_lookup",
                "Customer asks to recall current conversation context.",
                confidence=0.9,
                workflow_relation="contextual_followup",
                preserve_active_workflow=bool(state.get("active_workflow")),
                kind="contextual_followup",
            )
            next_state["llm_router_result"] = _router_result_summary(
                "fallback",
                mode="guarded_authoritative",
                fallback_reason="conversation_memory_guard",
                fallback_to_deterministic=True,
            )
            return next_state
        if not llm_intent_service or not hasattr(llm_intent_service, "route"):
            return _router_fallback_state(state, "missing_provider", "guarded_authoritative", fallback_to_deterministic)

        payload = {**_build_llm_router_payload(state), "router_mode": llm_router_mode}
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
            "faq_intent": (
                (decision.get("faq_intent") or decision["intent"])
                if decision["route"] == "faq"
                else decision.get("faq_intent")
            ),
            "retrieval_query": decision.get("retrieval_query") or decision.get("faq_query"),
            "risk_level": decision.get("risk_level"),
            "requires_human": decision.get("requires_human"),
            "requires_backend": decision.get("requires_backend"),
            "missing_slots": list(decision.get("missing_slots") or []),
            "workflow_relation": decision.get("workflow_relation"),
            "preserve_active_workflow": decision.get("preserve_active_workflow"),
        }
        route = "final_reply" if decision["route"] == "unsupported" else decision["route"]
        next_state = {
            **state,
            "intent_result": intent_result,
            "llm_router_result": _router_result_summary("accepted", decision=decision, provider=provider, mode=mode),
            "route": route,
            "route_source": "llm_guarded_authoritative",
        }
        if route == "final_reply":
            kind = decision.get("workflow_relation") if decision.get("workflow_relation") in {"acknowledgement", "contextual_followup"} else None
            if not kind:
                if decision["intent"] == "casual_chat":
                    kind = "casual_chat"
                elif decision["intent"] == "conversation_memory_lookup":
                    kind = "contextual_followup"
                else:
                    kind = "clarification"
            return _prepare_final_reply_state(next_state, str(kind))
        return next_state

    return node


def sop_node(state: GraphState) -> GraphState:
    if state.get("active_workflow") and _current_workflow_resolution_relation(state):
        if has_workflow_resolution_signal(state):
            return handle_waiting_backend(state)
    if state.get("workflow_stage") in {"waiting_backend", "backend_querying", "backend_replied", "waiting_customer_supplement"}:
        return handle_waiting_backend(state)
    return run_sop(state)


def _current_workflow_resolution_relation(state: dict[str, Any]) -> bool:
    if (state.get("intent_result") or {}).get("workflow_relation") == "current_workflow_resolution":
        return True
    for key in ("llm_sop_dialogue_plan", "llm_sop_slot_result"):
        plan = state.get(key)
        if not isinstance(plan, dict):
            continue
        nested = plan.get("result") if isinstance(plan.get("result"), dict) else {}
        relation = plan.get("intent_relation") or nested.get("intent_relation")
        if relation == "current_workflow_resolution":
            return True
    return False


def rag_node(state: GraphState) -> GraphState:
    state = {**state, "rag_context": prepare_faq_context_for_delivery(state.get("rag_context"), state)}
    rag_result = answer_from_rag_context(state) if state.get("rag_context") is not None else answer_from_static_knowledge(state)
    fallback_text = rag_result["answer"]
    next_state = {**state}
    if next_state.get("active_workflow"):
        next_state["active_workflow"] = None
        next_state["workflow_stage"] = None
    return {
        **next_state,
        "rag_result": rag_result,
        "node_reply_template": "faq_answer",
        "node_facts": {
            "answer": rag_result.get("answer"),
            "matched": rag_result.get("matched"),
            "source": rag_result.get("source"),
            "query": rag_result.get("query"),
            "fallback_reason": rag_result.get("fallback_reason"),
            "documents": rag_result.get("documents"),
            "faq_intent": _faq_intent_from_state(state),
        },
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_plan": build_reply_plan(
            kind="faq_answer",
            fallback_text=fallback_text,
            must_say=[],
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


def make_final_reply_node(
    final_reply_service=None,
    *,
    llm_final_reply_enabled: bool = False,
    llm_final_reply_streaming_enabled: bool = False,
):
    async def node(state: GraphState) -> GraphState:
        if llm_final_reply_streaming_enabled and state.get("channel_type") == "livechat":
            return {
                **state,
                "response_text_fallback": state.get("response_text_fallback") or state.get("response_text"),
                "final_response_text": None,
                "final_reply_result": {"status": "skipped", "reason": "streaming_enabled"},
            }
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
        "node_reply_template": "human_handoff",
        "node_facts": {
            "reason": reason,
            "handoff_requested": True,
            "allowed_facts": ["客户需要真人客服", "系统将提出转接请求"],
        },
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
    fallback_text = "我理解你现在很着急。我会先尽力说明处理方式；如果你愿意，也可以直接告诉我需要转接真人客服。"
    return {
        **state,
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "node_reply_template": "emotion_care",
        "node_facts": {
            "fallback_text": fallback_text,
            "risk_level": (state.get("intent_result") or {}).get("risk_level"),
        },
        "reply_plan": build_reply_plan(
            kind="emotion_care",
            fallback_text=fallback_text,
            must_say=["理解", "转接真人客服"],
            semantic_required_items=["human_handoff_notice"],
            must_not_say=["已到账", "已完成", "保证"],
            allowed_facts=["客户情绪较急", "可以请求转接真人客服"],
        ),
        "commands": [],
    }

def command_planner_node(state: GraphState) -> GraphState:
    commands = list(state.get("commands") or [])
    text = state.get("final_response_text") or state.get("response_text")
    if text:
        target_command = _final_reply_target_text_command(commands)
        if target_command is not None:
            target_command["payload"] = {**dict(target_command.get("payload") or {}), "text": text}
        else:
            for command in commands:
                if str(command.get("type")) == str(CommandType.LIVECHAT_SEND_TEXT) and not _final_reply_exempt(command):
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


def _final_reply_target_text_command(commands: list[dict[str, Any]]) -> dict[str, Any] | None:
    for command in commands:
        if str(command.get("type")) != str(CommandType.LIVECHAT_SEND_TEXT):
            continue
        if (command.get("payload") or {}).get("final_reply_target") is True:
            return command
    return None


def _final_reply_exempt(command: dict[str, Any]) -> bool:
    return (command.get("payload") or {}).get("final_reply_exempt") is True


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
        result["retrieval_query"] = faq_query
    if route == "faq":
        result["faq_intent"] = intent
    if emotion:
        result["emotion"] = emotion
    if risk_level:
        result["risk_level"] = risk_level
    if workflow_relation:
        result["workflow_relation"] = workflow_relation
    if preserve_active_workflow is not None:
        result["preserve_active_workflow"] = preserve_active_workflow
    return result


def _faq_intent_from_state(state: dict[str, Any]) -> str | None:
    intent_result = state.get("intent_result") or {}
    value = intent_result.get("faq_intent") or intent_result.get("intent")
    return str(value) if value else None


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


def _image_candidate_only_route(state: GraphState) -> GraphState | None:
    if state.get("active_workflow"):
        return None
    if state.get("event_type") != "FILE_RECEIVED":
        return None
    if normalize_text(state.get("rewritten_question") or state.get("raw_user_input")):
        return None
    analysis = _image_analysis_from_state(state)
    if not analysis:
        return None
    candidate = _candidate_from_image_analysis(analysis)
    if candidate == "deposit":
        text = "我看到这张图片可能是存款凭证。请问你要查询存款未到账或存款相关问题吗？"
        intent = "image_deposit_candidate"
    elif candidate == "withdrawal":
        text = "我看到这张图片可能是提款凭证。请问你要查询提款未到账或提款相关问题吗？"
        intent = "image_withdrawal_candidate"
    else:
        text = "我已收到图片。请补充你要咨询的问题，我会继续协助。"
        intent = "image_unknown"
    slot_memory = dict(state.get("slot_memory") or {})
    if candidate in {"deposit", "withdrawal"}:
        slot_memory["pending_image_candidates"] = [_pending_image_candidate_record(state, analysis, candidate)]
    return _prepare_image_final_reply_state(state, slot_memory, intent, text, analysis)


def _promote_pending_image_candidate_route(state: GraphState, text: str) -> GraphState | None:
    if state.get("active_workflow"):
        return None
    slot_memory = dict(state.get("slot_memory") or {})
    candidates = [
        candidate
        for candidate in (slot_memory.get("pending_image_candidates") or [])
        if isinstance(candidate, dict)
    ]
    if not candidates:
        return None
    candidate = candidates[-1]
    kind = _candidate_from_image_analysis(candidate)
    if kind not in {"deposit", "withdrawal"}:
        return None
    if not (_confirms_image_candidate(text, kind) or _has_sop_key_material(text)):
        return None
    intent = "deposit_missing" if kind == "deposit" else "withdrawal_missing"
    url = candidate.get("attachment_url") or candidate.get("url")
    verified = {
        "url": url,
        "receipt_kind": kind,
        "verified_receipt_attachment": True,
        "source": "image_candidate_confirmation",
    }
    slot_memory["verified_receipt_attachments"] = [verified] if url else []
    if url:
        screenshot_key = "deposit_screenshot" if kind == "deposit" else "withdrawal_screenshot"
        slot_memory["receipt_screenshot"] = url
        slot_memory[screenshot_key] = url
    return _with_route(
        {**state, "slot_memory": slot_memory, "verified_receipt_attachments": slot_memory["verified_receipt_attachments"]},
        intent,
        "sop",
        "Customer confirmed an image candidate or supplied SOP key material after image analysis.",
        sop_name=intent,
        workflow_relation="current_workflow_supplement",
        preserve_active_workflow=True,
    )


def _prepare_image_final_reply_state(
    state: GraphState,
    slot_memory: dict[str, Any],
    intent: str,
    text: str,
    analysis: dict[str, Any],
) -> GraphState:
    return {
        **state,
        "slot_memory": slot_memory,
        "route": "final_reply",
        "route_source": "image_analysis_candidate",
        "image_candidate_only": True,
        "pending_image_confirmation": analysis,
        "active_workflow": None,
        "workflow_stage": state.get("workflow_stage"),
        "intent_result": {
            "intent": intent,
            "route": "final_reply",
            "confidence": float(analysis.get("confidence") or 0.0),
            "reason": "Image analysis can only create a candidate and must ask for confirmation.",
        },
        "response_text": text,
        "response_text_fallback": text,
        "node_reply_template": "image_candidate_confirmation",
        "node_facts": {
            "image_analysis": analysis,
            "allowed_facts": ["图片解析结果仅作为候选意图，需要客户确认"],
            "fallback_text": text,
        },
        "reply_plan": build_reply_plan(
            kind="clarification",
            fallback_text=text,
            must_say=[],
            must_not_say=["已到账", "已完成", "已处理", "已提交", "已转接"],
            allowed_facts=["图片解析结果仅作为候选意图，需要客户确认"],
        ),
        "commands": [{"type": CommandType.LIVECHAT_SEND_TEXT, "payload": {"text": text}}],
    }


def _candidate_from_image_analysis(analysis: dict[str, Any]) -> str:
    intents = {str(item) for item in analysis.get("candidate_intents") or []}
    receipt_kind = str(analysis.get("receipt_kind") or "").lower()
    if analysis.get("is_receipt_like") and (receipt_kind == "deposit" or "deposit_missing_candidate" in intents):
        return "deposit"
    if analysis.get("is_receipt_like") and (receipt_kind == "withdrawal" or "withdrawal_missing_candidate" in intents):
        return "withdrawal"
    return "unknown"


def _pending_image_candidate_record(state: GraphState, analysis: dict[str, Any], kind: str) -> dict[str, Any]:
    attachment = _first_image_attachment(state)
    return {
        "attachment_url": analysis.get("attachment_url") or (attachment or {}).get("url"),
        "content_type": analysis.get("content_type") or (attachment or {}).get("content_type") or (attachment or {}).get("mime_type"),
        "candidate_intents": list(analysis.get("candidate_intents") or []),
        "candidate_slots": dict(analysis.get("candidate_slots") or {}),
        "confidence": float(analysis.get("confidence") or 0.0),
        "evidence_summary": analysis.get("evidence_summary"),
        "is_receipt_like": bool(analysis.get("is_receipt_like")),
        "receipt_kind": kind,
    }


def _first_image_attachment(state: GraphState) -> dict[str, Any] | None:
    for attachment in state.get("attachments") or []:
        content_type = str(attachment.get("content_type") or attachment.get("mime_type") or "")
        if content_type.startswith("image/") or attachment.get("url"):
            return attachment
    return None


def _image_analysis_from_state(state: GraphState) -> dict[str, Any] | None:
    analysis = state.get("image_analysis")
    if isinstance(analysis, dict) and analysis:
        return analysis
    for attachment in state.get("attachments") or []:
        item = attachment.get("image_analysis")
        if isinstance(item, dict) and item:
            return item
    return None


def _confirms_image_candidate(text: str, kind: str) -> bool:
    lower = normalize_text(text).lower()
    if not lower:
        return False
    yes = _contains_any(lower, ("是", "对", "對", "确认", "確認", "帮我查", "查", "yes", "correct", "sí", "si"))
    if not yes:
        return False
    if kind == "deposit":
        return _contains_any(lower, ("存款", "充值", "deposit", "depósito", "deposito", "recarga")) or len(lower) <= 16
    return _contains_any(lower, ("提款", "提现", "withdraw", "withdrawal", "retiro")) or len(lower) <= 16


def _has_sop_key_material(text: str) -> bool:
    return bool(extract_identity(text) or extract_order_id(text) or extract_amount(text))


def _auto_handoff_route(state: GraphState, lower: str, hints: dict[str, Any]) -> GraphState | None:
    active = str(state.get("active_workflow") or "")
    stage = str(state.get("workflow_stage") or "")
    if active and stage in ACTIVE_WORKFLOW_GUARD_STAGES and _active_workflow_conflict_has_data(state, active):
        return _with_route(
            state,
            "active_workflow_conflict_with_data",
            "human_handoff",
            "Message conflicts with the active SOP after customer data was already collected.",
            confidence=0.88,
            workflow_relation="human_escalation",
            preserve_active_workflow=True,
        )
    if _is_screenshot_upload_failed(lower, hints):
        return _with_route(
            state,
            "screenshot_upload_failed",
            "human_handoff",
            "Customer cannot upload or continue with a required screenshot or attachment.",
            confidence=0.9,
        )
    if _is_wallet_identity_risk(lower):
        return _with_route(
            state,
            "wallet_identity_risk",
            "human_handoff",
            "Wallet, bank card, receiving account, identity, or KYC issue requires manual support.",
            confidence=0.9,
        )
    if _is_account_verification_issue(lower):
        return _with_route(
            state,
            "account_verification_issue",
            "human_handoff",
            "Verification code, SIM, phone, or email verification issue requires manual support.",
            confidence=0.9,
        )
    if _is_promo_refund_unsupported(lower):
        return _with_route(
            state,
            "promo_refund_unsupported",
            "human_handoff",
            "Promotion, bonus, registration, refund, or unsupported commercial issue is outside FAQ/SOP scope.",
            confidence=0.88,
        )
    if _is_unsupported_concrete_issue(lower):
        return _with_route(
            state,
            "game_technical_issue",
            "human_handoff",
            "Technical/game-specific issues are out of FAQ/SOP scope.",
            confidence=0.88,
        )
    if _is_abuse_or_fraud_risk(lower):
        return _with_route(
            state,
            "abuse_or_fraud_risk",
            "human_handoff",
            "Fraud, fund-safety, or severe abuse concern requires manual support.",
            confidence=0.9,
            emotion="distressed",
            risk_level="high",
        )
    if _is_tutorial_failed_aftercare(lower):
        return _with_route(
            state,
            "tutorial_failed_aftercare",
            "human_handoff",
            "Customer reports the FAQ/tutorial was followed but still failed.",
            confidence=0.86,
        )
    return None


def _final_reply_route(
    state: GraphState,
    intent: str,
    reason: str,
    confidence: float = 0.9,
    *,
    workflow_relation: str | None = None,
    preserve_active_workflow: bool | None = None,
    kind: str,
) -> GraphState:
    next_state = _with_route(
        state,
        intent,
        "final_reply",
        reason,
        confidence=confidence,
        workflow_relation=workflow_relation,
        preserve_active_workflow=preserve_active_workflow,
    )
    return _prepare_final_reply_state(next_state, kind)


def _prepare_final_reply_state(state: GraphState, kind: str) -> GraphState:
    if kind == "acknowledgement":
        fallback_text = _acknowledgement_reply_text(state)
        template = "acknowledgement"
        must_not_say = ["已到账", "已完成", "已处理", "已同步", "已补充给后台", "tg:"]
        allowed_facts = [fallback_text]
    elif kind == "contextual_followup":
        fallback_text = _contextual_followup_reply_text(state)
        template = "contextual_followup"
        must_not_say = ["已到账", "已完成", "已处理", "已同步", "已补充给后台", "tg:"]
        allowed_facts = [fallback_text]
    elif kind == "casual_chat":
        fallback_text = _casual_reply_text(str(state.get("reply_language") or state.get("conversation_language") or "es"))
        template = "default_final_reply"
        must_not_say = ["已到账", "已完成", "已处理", "已同步", "tg:"]
        allowed_facts = _system_capability_facts() + ["用户发送非业务闲聊或问候"]
    else:
        fallback_text = _clarification_reply_text(str(state.get("reply_language") or state.get("conversation_language") or "es"))
        template = "clarification"
        kind = "clarification"
        must_not_say = ["已到账", "已完成", "已处理"]
        allowed_facts = ["需要客户补充问题类型"]

    return {
        **state,
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "node_reply_template": template,
        "node_facts": {
            "fallback_text": fallback_text,
            "workflow_relation": (state.get("intent_result") or {}).get("workflow_relation"),
            "supported_topics": ["存款", "提款", "流水", "截图", "账号访问", "真人客服"],
            "allowed_facts": allowed_facts,
        },
        "reply_plan": build_reply_plan(
            kind=kind,
            fallback_text=fallback_text,
            must_say=["存款", "提款", "流水", "真人客服"] if kind == "clarification" else [],
            must_not_say=must_not_say,
            allowed_facts=allowed_facts,
            metadata={"workflow_relation": (state.get("intent_result") or {}).get("workflow_relation")},
        ),
        "commands": [],
    }


def _active_workflow_deterministic_route(state: GraphState, active_workflow: str) -> GraphState:
    if _is_acknowledgement(state):
        return _final_reply_route(
            state,
            "acknowledgement",
            "Customer acknowledged the previous message; preserve active workflow without SOP side effects.",
            confidence=0.88,
            workflow_relation="acknowledgement",
            preserve_active_workflow=True,
            kind="acknowledgement",
        )
    if _is_contextual_followup(state):
        return _final_reply_route(
            state,
            "contextual_followup",
            "Customer asks a context-dependent follow-up about the active workflow requirements.",
            confidence=0.86,
            workflow_relation="contextual_followup",
            preserve_active_workflow=True,
            kind="contextual_followup",
        )
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    if _is_rollover_explanation(text):
        return _final_reply_route(
            state,
            "contextual_followup",
            "Rollover explanation is outside canonical FAQ and should be handled as a contextual final reply.",
            confidence=0.78,
            workflow_relation="contextual_followup",
            preserve_active_workflow=True,
            kind="contextual_followup",
        )
    faq = _is_independent_faq_during_workflow(state)
    if faq:
        return _with_route(
            state,
            faq["intent"],
            "faq",
            "Independent FAQ during active workflow; clear the previous SOP state after answering.",
            confidence=0.86,
            faq_query=faq["faq_query"],
            workflow_relation="independent_faq",
            preserve_active_workflow=False,
        )
    if _is_cross_workflow_business_object(state, active_workflow):
        return _final_reply_route(
            state,
            "clarification_needed",
            "Message mentions a different business object than the active workflow; ask before changing state.",
            confidence=0.78,
            workflow_relation="new_workflow_request",
            preserve_active_workflow=True,
            kind="clarification",
        )
    if _is_new_workflow_request(state, active_workflow):
        return _final_reply_route(
            state,
            "clarification_needed",
            "New SOP-like request during active workflow; ask whether to switch workflow before changing state.",
            confidence=0.78,
            workflow_relation="new_workflow_request",
            preserve_active_workflow=True,
            kind="clarification",
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
    return _final_reply_route(
        state,
        "clarification_needed",
        "Message relation to the active workflow is unclear.",
        confidence=0.55,
        workflow_relation="unclear",
        preserve_active_workflow=True,
        kind="clarification",
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
    if _is_conversation_memory_lookup(state):
        return True
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


def _is_conversation_memory_lookup(state: GraphState) -> bool:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    if not text:
        return False
    return any(
        re.search(pattern, text, flags=re.I)
        for pattern in (
            r"(刚刚|剛剛|上一句|上句|前一句|刚才|剛才).*(说|說|回复|回覆|讲|講|问|問|提到)",
            r"(我|你).*(刚刚|剛剛|上一句|上句|前一句|刚才|剛才).*(说|說|回复|回覆|讲|講|问|問|提到)",
            r"(刚刚|剛剛|刚才|剛才|之前).*(原因|为什么|為什麼|为何|為何|导致|導致).*(无法提款|無法提款|提款|提现|提現|流水)",
            r"\bwhat did i just say\b",
            r"\bwhat was my previous message\b",
            r"\bwhat did you just say\b",
            r"\bwhat did you reply\b",
        )
    )


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
    language = str(state.get("reply_language") or state.get("conversation_language") or "es").lower()
    stage = str(state.get("workflow_stage") or "")
    if language.startswith("en"):
        if stage in {"waiting_backend", "backend_querying"}:
            return "Got it. The case is still being checked, and I will update you here once there is progress."
        if stage == "waiting_customer_supplement":
            return "Got it. Please send the requested details here when you have them, and I will continue helping you check this."
        return "Got it. You can send the requested details here whenever you are ready."
    if language.startswith("es"):
        if stage in {"waiting_backend", "backend_querying"}:
            return "Entendido. El caso sigue en revisión y te avisaré aquí cuando haya novedades."
        if stage == "waiting_customer_supplement":
            return "Entendido. Envíame aquí los datos solicitados cuando los tengas y seguiré ayudándote a revisarlo."
        return "Entendido. Puedes enviar aquí los datos solicitados cuando estés listo."
    if stage in {"waiting_backend", "backend_querying"}:
        return "收到，案件仍在确认中，有更新会在这里通知你。"
    if stage == "waiting_customer_supplement":
        return "收到，请你确认后把需要补充的资料发给我，我会继续协助核实。"
    return "收到，你准备好后可以继续把需要补充的资料发给我。"


def _contextual_followup_reply_text(state: GraphState) -> str:
    memory_text = _conversation_memory_reply_text(state)
    if memory_text:
        return memory_text
    language = str(state.get("reply_language") or state.get("conversation_language") or "es").lower()
    active = str(state.get("active_workflow") or "")
    if language.startswith("en"):
        if active == "withdrawal_missing":
            return (
                "Yes, you may provide your name, but for checking this withdrawal case we still need your registered "
                "phone number and a screenshot of the withdrawal request or receipt. Your name alone may not be enough "
                "to locate the record."
            )
        return "Yes, you may provide your name, but we may still need the requested account details and screenshot to continue checking."
    if language.startswith("es"):
        if active == "withdrawal_missing":
            return (
                "Sí, puedes proporcionar tu nombre, pero para revisar este retiro todavía necesitamos tu número de "
                "teléfono registrado y una captura de la solicitud de retiro o el comprobante. Solo el nombre puede "
                "no ser suficiente para localizar el registro."
            )
        return "Sí, puedes proporcionar tu nombre, pero quizá aún necesitemos los datos de la cuenta y la captura solicitada para continuar revisando."
    if active == "withdrawal_missing":
        return "可以提供姓名，但为了查询这笔提款，我们仍需要你的注册手机号和提款截图或凭证。只有姓名可能无法准确核实记录。"
    return "可以提供姓名，但为了继续核实，我们可能仍需要你按前面要求提供账号资料和截图。"


def _casual_reply_text(language: str) -> str:
    if language.lower().startswith("en"):
        return "Hello, I can help with deposits, withdrawals, rollover requirements, screenshots, account access, and human support requests."
    if language.lower().startswith("es"):
        return "Hola, puedo ayudar con depósitos, retiros, requisitos de rollover, capturas, acceso a la cuenta y solicitudes de atención humana."
    return "你好，我可以协助处理存款、提款、流水要求、截图凭证、账号访问问题，也可以帮你请求真人客服。"


def _clarification_reply_text(language: str) -> str:
    normalized = language.lower()
    if normalized.startswith("en"):
        return "Please tell me what you need help with, or choose deposits, withdrawals, rollover, or human support."
    if normalized.startswith("es"):
        return "Indícame con qué necesitas ayuda, o elige depósitos, retiros, rollover o atención humana."
    return "请补充你要咨询的问题，或说明是存款、提款、流水还是需要真人客服。"


def _conversation_memory_reply_text(state: GraphState) -> str | None:
    if not _is_conversation_memory_lookup(state):
        return None
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()
    if _is_withdrawal_reason_recall(text):
        reason_text = _recent_withdrawal_reason_reply_text(state)
        if reason_text:
            return f"刚才查询结果显示：{reason_text}"
    wants_assistant = _contains_any(text, ("你刚", "你剛", "你上", "你回复", "你回覆", "what did you", "you reply"))
    target_role = "assistant" if wants_assistant else "customer"
    previous = _previous_message_text(state, target_role)
    if previous:
        if target_role == "assistant":
            return f"我刚才回复的是：{previous}"
        return f"你上一句说的是：{previous}"
    return "我这里暂时没有可确认的上一句聊天内容。请你再说明一次需要处理的问题，我会继续协助。"


def _is_withdrawal_reason_recall(text: str) -> bool:
    return _contains_any(text, ("无法提款", "無法提款", "提款", "提现", "提現", "withdraw")) and _contains_any(
        text,
        ("原因", "为什么", "為什麼", "为何", "為何", "导致", "導致", "流水", "reason", "why"),
    )


def _recent_withdrawal_reason_reply_text(state: GraphState) -> str | None:
    for message in reversed(list(state.get("recent_messages") or [])):
        role = str(message.get("sender_role") or "")
        if role not in {"assistant", "backend"}:
            continue
        text = normalize_text(message.get("text_content") or message.get("text") or message.get("content"))
        if not text:
            continue
        if _contains_any(text, ("剩余流水", "剩餘流水", "未完成的流水", "流水要求", "提款限制", "无法提款", "無法提款")):
            return _strip_customer_greeting(text)
    return None


def _strip_customer_greeting(text: str) -> str:
    stripped = normalize_text(text)
    return re.sub(r"^(您好|你好|您好！|你好！)[，,。!！\s]*", "", stripped).strip()


def _previous_message_text(state: GraphState, sender_role: str) -> str | None:
    current = normalize_text(state.get("raw_user_input"))
    for message in reversed(list(state.get("recent_messages") or [])):
        role = str(message.get("sender_role") or "")
        if role != sender_role:
            continue
        text = normalize_text(message.get("text_content") or message.get("text") or message.get("content"))
        if not text or text == current:
            continue
        return text
    return None


def _system_capability_facts() -> list[str]:
    return [
        "支持存款问题",
        "支持提款问题",
        "支持流水要求查询",
        "支持截图或凭证补充",
        "支持账号访问或忘记密码说明",
        "支持请求真人客服",
    ]


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
        next_state = _prepare_final_reply_state({
            **state,
            "intent_result": {
                "intent": "clarification_needed",
                "route": "final_reply",
                "confidence": 0.0,
                "reason": "LLM intent classifier failed and deterministic fallback is disabled.",
            },
            "route": "final_reply",
            "route_source": "llm_guarded_authoritative",
        }, "clarification")
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
    if _is_withdrawal_howto(text) or _contains_any(text, ("怎么提款", "怎么提现", "如何提现", "咋取钱", "怎么取钱", "如何取钱")):
        return {"intent": "withdrawal_howto", "faq_query": "如何提款"}
    if _is_forgot_password_howto(text):
        return {"intent": "forgot_password_howto", "faq_query": "忘记密码"}
    if _is_screenshot_upload_howto(text, extract_route_hints(state)) or _contains_any(text, ("怎么上传截图", "如何上传截图")):
        return {"intent": "screenshot_upload_howto", "faq_query": "上传截图"}
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
    return [
        {
            "url": item.get("url"),
            "name": item.get("name"),
            "mime_type": item.get("mime_type"),
            "content_type": item.get("content_type"),
            "image_analysis_status": item.get("image_analysis_status"),
            "image_candidate_id": item.get("image_candidate_id"),
            "verified_receipt_attachment": item.get("verified_receipt_attachment"),
            "receipt_kind": item.get("receipt_kind"),
        }
        for item in state.get("attachments") or []
    ]


def _default_reply_language(tenant_default: str, fallback: str, supported: list[str]) -> str:
    for candidate in (tenant_default, fallback):
        normalized = normalize_language_code(candidate)
        if normalized != "unknown" and normalized in supported:
            return normalized
    return supported[0] if supported else "es"


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


def _extract_button_id(payload: dict[str, Any]) -> str | None:
    event = payload.get("event") or {}
    candidates = (
        payload.get("button_id"),
        payload.get("postback_id"),
        event.get("button_id") if isinstance(event, dict) else None,
        event.get("postback_id") if isinstance(event, dict) else None,
        ((event.get("postback") or {}).get("id") if isinstance(event.get("postback"), dict) else None) if isinstance(event, dict) else None,
    )
    for candidate in candidates:
        value = normalize_text(candidate)
        if value:
            return value
    return None


def _apply_menu_button_route(state: GraphState) -> GraphState | None:
    payload = state.get("payload_json") or {}
    raw_text = normalize_text(state.get("raw_user_input") or _extract_text(payload))
    slot_memory = dict(state.get("slot_memory") or {})
    livechat_menu = dict(slot_memory.get("livechat_menu") or {})
    context = livechat_menu.get("context")
    button_id = _extract_button_id(payload)
    if not button_id and context:
        button_id = detect_button_id(
            raw_text,
            menu_context=context,
            language=state.get("reply_language") or slot_memory.get("last_reply_language"),
        )
    if not button_id:
        return None

    current_context = context or "main"
    slot_memory["livechat_menu"] = _next_menu_memory(livechat_menu, current_context, button_id)
    base = {
        **state,
        "slot_memory": slot_memory,
        "rewritten_question": raw_text,
        "rewrite_result": {
            "rewritten_question": raw_text,
            "language": "unknown",
            "detected_language": "unknown",
            "language_confidence": 0.0,
            "language_source": "livechat_button",
            "mentioned_entities": {},
            "notes": [f"livechat_button_id:{button_id}"],
        },
        "rewrite_source": "livechat_button",
        "route_locked": True,
    }

    menu_key = _navigation_menu_key(button_id, livechat_menu)
    if menu_key:
        return {
            **base,
            "route": "final_reply",
            "route_source": "livechat_button",
            "intent_result": {
                "intent": "menu_navigation",
                "route": "final_reply",
                "confidence": 1.0,
                "reason": f"LiveChat menu navigation button {button_id}.",
                "menu_key": menu_key,
            },
            "commands": [
                {
                    "type": CommandType.LIVECHAT_SEND_BUTTONS,
                    "payload": {"menu_key": menu_key, "language": _menu_language_for_state(base)},
                }
            ],
        }

    route = BUSINESS_BUTTON_ROUTES.get(button_id)
    if not route:
        return None
    is_repeat_active_sop = (
        route.get("route") == "sop"
        and state.get("active_workflow") == route.get("intent")
        and state.get("workflow_stage") == "collecting_slots"
    )
    intent_result = {
        "intent": route["intent"],
        "route": route["route"],
        "confidence": 1.0,
        "reason": (
            f"Repeated LiveChat button {button_id} for active SOP."
            if is_repeat_active_sop
            else f"LiveChat button {button_id}."
        ),
        "sop_name": route.get("sop_name"),
        "faq_query": route.get("faq_query"),
    }
    if route.get("route") == "faq":
        intent_result["faq_trigger_source"] = LIVECHAT_BUTTON_SOURCE
    if is_repeat_active_sop:
        intent_result["workflow_relation"] = "current_workflow_supplement"
        intent_result["preserve_active_workflow"] = True
    return {
        **base,
        "route": route["route"],
        "route_source": "livechat_button",
        "intent_result": intent_result,
    }


def _navigation_menu_key(button_id: str, livechat_menu: dict[str, Any]) -> str | None:
    if button_id in MENU_BY_NAV_BUTTON:
        return MENU_BY_NAV_BUTTON[button_id]
    if button_id == "route_main":
        return "main"
    if button_id == "route_previous":
        return str(livechat_menu.get("previous_context") or "main")
    return None


def _menu_language_for_state(state: GraphState) -> str:
    slot_memory = state.get("slot_memory") or {}
    for value in (
        state.get("reply_language"),
        slot_memory.get("last_reply_language"),
        state.get("conversation_language"),
        state.get("detected_language"),
    ):
        normalized = normalize_language_code(value)
        if normalized != "unknown":
            return normalized
    return "es"


def _next_menu_memory(livechat_menu: dict[str, Any], context: str, button_id: str) -> dict[str, Any]:
    preserved_intro = {
        key: livechat_menu[key]
        for key in ("intro_sent", "intro_thread_id", "intro_sent_threads")
        if key in livechat_menu
    }
    if button_id in MENU_BY_NAV_BUTTON:
        return {**preserved_intro, "context": MENU_BY_NAV_BUTTON[button_id], "previous_context": context, "intro_sent": livechat_menu.get("intro_sent", True)}
    if button_id == "route_main":
        return {**preserved_intro, "context": "main", "previous_context": context, "intro_sent": livechat_menu.get("intro_sent", True)}
    if button_id == "route_previous":
        previous = str(livechat_menu.get("previous_context") or "main")
        return {**preserved_intro, "context": previous, "previous_context": "main", "intro_sent": livechat_menu.get("intro_sent", True)}
    return {**livechat_menu, "context": context, "intro_sent": livechat_menu.get("intro_sent", True)}


def _extract_attachments(payload: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    attachments = [_sanitize_attachment(item) for item in (payload.get("attachments") or []) if isinstance(item, dict)]
    event = payload.get("event") or {}
    if event_type == "FILE_RECEIVED":
        file_payload = event.get("file") if isinstance(event.get("file"), dict) else event
        attachment = _sanitize_attachment(file_payload)
        if attachment:
            attachments.append(attachment)
    return [attachment for attachment in attachments if attachment]


def _sanitize_attachment(item: dict[str, Any]) -> dict[str, Any] | None:
    url = item.get("url") or item.get("content_url") or item.get("thumbnail_url")
    if not url:
        return None
    content_type = item.get("content_type") or item.get("mime_type")
    attachment = {
        "url": url,
        "name": item.get("name") or item.get("filename"),
        "mime_type": item.get("mime_type") or content_type,
        "content_type": content_type or item.get("mime_type"),
        "image_analysis_status": item.get("image_analysis_status"),
        "image_candidate_id": item.get("image_candidate_id"),
        "image_analysis": item.get("image_analysis"),
        "verified_receipt_attachment": item.get("verified_receipt_attachment"),
        "receipt_kind": item.get("receipt_kind"),
    }
    return {key: value for key, value in attachment.items() if value is not None}


def _extract_image_analysis(payload: dict[str, Any], attachments: list[dict[str, Any]]) -> dict[str, Any] | None:
    event = payload.get("event") or {}
    candidates = (
        payload.get("image_analysis"),
        event.get("image_analysis") if isinstance(event, dict) else None,
        *((attachment.get("image_analysis") for attachment in attachments),),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            attachment = attachments[0] if attachments else {}
            result = dict(candidate)
            result.setdefault("attachment_url", attachment.get("url"))
            result.setdefault("content_type", attachment.get("content_type") or attachment.get("mime_type"))
            return result
    return None


def _detect_language(text: str) -> str:
    return detect_language_deterministic(text)["detected_language"]


def _is_deposit_missing(text: str) -> bool:
    return _contains_any(text, ("deposit", "depósito", "deposito", "存款", "充值")) and _contains_any(
        text, ("no llegó", "no llego", "未到账", "没到账", "no acreditado", "nunca me pagaron")
    )


def _is_withdrawal_missing(text: str) -> bool:
    return _contains_any(text, ("retiro", "withdrawal", "提款", "提现")) and _contains_any(
        text, ("no llegó", "no llego", "未到账", "没到账", "no acreditado", "nunca me pagaron", "did not arrive")
    )


def _is_withdrawal_blocked_or_rollover(text: str) -> bool:
    return _contains_any(text, ("no puedo retirar", "无法提款", "withdrawal blocked", "rollover", "流水"))


def _is_pending_reply_lookup(text: str) -> bool:
    return _contains_any(text, ("caso anterior", "previous case", "上一笔案件"))


def _is_deposit_howto(text: str) -> bool:
    return _contains_any(text, ("cómo recargar", "como recargar", "how to deposit", "如何充值", "充值方式"))


def _is_withdrawal_howto(text: str) -> bool:
    return _contains_any(text, ("cómo puedo retirar", "como puedo retirar", "cómo retirar", "como retirar", "how to withdraw", "如何提款", "咋取钱", "怎么取钱", "如何取钱"))


def _is_forgot_password_howto(text: str) -> bool:
    return _contains_any(text, ("forgot password", "olvidé mi contraseña", "olvide mi contraseña", "忘记密码"))


def _is_screenshot_upload_howto(text: str, hints: dict[str, Any]) -> bool:
    return hints.get("has_screenshot_signal") and _contains_any(text, ("subir", "upload", "enviar", "上传"))


def _is_screenshot_upload_failed(text: str, hints: dict[str, Any]) -> bool:
    if not (hints.get("has_screenshot_signal") or _contains_any(text, ("attachment", "file", "附件", "图片", "照片", "imagen", "archivo"))):
        return False
    return _contains_any(
        text,
        (
            "失败",
            "失敗",
            "传不了",
            "傳不了",
            "上传不了",
            "上傳不了",
            "发不了",
            "發不了",
            "无法上传",
            "無法上傳",
            "不能上传",
            "不能上傳",
            "upload failed",
            "can't upload",
            "cannot upload",
            "no puedo subir",
            "no me deja subir",
            "no puedo enviar",
        ),
    )


def _is_rollover_explanation(text: str) -> bool:
    return _contains_any(text, ("qué es rollover", "que es rollover", "what is rollover", "流水是什么", "rollover explanation"))


def _is_menu_help(text: str, hints: dict[str, Any]) -> bool:
    return hints.get("has_menu_signal") and _contains_any(text, ("no veo", "ningun", "ningún", "dónde", "donde", "where", "找不到"))


def _is_service_frustration(text: str) -> bool:
    return _contains_any(text, ("todo el tiempo lo mismo", "siempre lo mismo", "otra vez lo mismo", "same thing every time"))


def _emotion_context(lower: str) -> dict[str, str | None]:
    if _is_abusive_or_emotional(lower):
        return {"emotion": "distressed", "risk_level": "high"}
    if _is_service_frustration(lower):
        return {"emotion": "frustrated", "risk_level": "elevated"}
    return {"emotion": None, "risk_level": None}


def _is_unsupported_concrete_issue(text: str) -> bool:
    return _contains_any(
        text,
        ("problemas técnicos", "problemas tecnicos", "technical issue", "del juego", "game issue", "游戏技术", "遊戲技術", "游戏问题", "遊戲問題"),
    )


def _is_account_access_issue(text: str) -> bool:
    return _contains_any(text, ("no puedo entrar", "no puedo iniciar sesión", "no puedo iniciar sesion", "can't log in", "无法登录"))


def _is_account_profile_or_wallet_change(text: str) -> bool:
    return _contains_any(text, ("cambiar wallet", "change wallet", "cambiar perfil", "change profile", "cambiar correo", "cambiar telefono"))


def _is_wallet_identity_risk(text: str) -> bool:
    return _contains_any(
        text,
        (
            "cambiar wallet",
            "change wallet",
            "cambiar perfil",
            "change profile",
            "cambiar correo",
            "cambiar telefono",
            "wallet",
            "billetera",
            "bank card",
            "银行卡",
            "銀行卡",
            "钱包",
            "錢包",
            "收款账户",
            "收款帳戶",
            "身份资料",
            "身份資料",
            "实名",
            "實名",
            "kyc",
            "持有人不一致",
            "姓名不一致",
            "资料异常",
            "資料異常",
        ),
    )


def _is_account_verification_issue(text: str) -> bool:
    has_verification = _contains_any(
        text,
        (
            "验证码",
            "驗證碼",
            "verification code",
            "codigo de verificacion",
            "código de verificación",
            "sim",
            "email verification",
            "邮箱验证",
            "郵箱驗證",
            "手机验证",
            "手機驗證",
            "phone verification",
        ),
    )
    has_problem = _contains_any(
        text,
        (
            "收不到",
            "没收到",
            "未收到",
            "無法",
            "无法",
            "不通过",
            "失效",
            "not receive",
            "did not receive",
            "no llega",
            "no recibo",
            "can't verify",
            "cannot verify",
        ),
    )
    return has_verification and has_problem


def _is_promo_refund_unsupported(text: str) -> bool:
    return _contains_any(
        text,
        (
            "优惠码",
            "優惠碼",
            "promo code",
            "codigo promocional",
            "código promocional",
            "bonus",
            "bono",
            "free spins",
            "注册问题",
            "註冊問題",
            "registration issue",
            "refund",
            "退款",
            "退回",
            "devolver",
            "reembolso",
        ),
    )


def _is_abuse_or_fraud_risk(text: str) -> bool:
    return _contains_any(
        text,
        (
            "诈骗",
            "詐騙",
            "骗子",
            "騙子",
            "fraud",
            "scam",
            "estafa",
            "资金安全",
            "資金安全",
            "fund safety",
            "account safety",
            "不安全",
            "安全风险",
            "安全風險",
            "mierda",
            "fuck",
        ),
    )


def _is_tutorial_failed_aftercare(text: str) -> bool:
    has_tutorial = _contains_any(
        text,
        (
            "教程",
            "教學",
            "说明",
            "說明",
            "guide",
            "tutorial",
            "instrucciones",
            "pasos",
            "按照你说",
            "照你说",
            "按你说",
        ),
    )
    still_failed = _contains_any(
        text,
        (
            "还是不行",
            "還是不行",
            "仍然不行",
            "还是失败",
            "還是失敗",
            "still not work",
            "still doesn't work",
            "still failed",
            "sigue sin funcionar",
            "no funciona",
        ),
    )
    return has_tutorial and still_failed


def _is_abusive_or_emotional(text: str) -> bool:
    return _contains_any(text, ("basura", "mierda", "estafa", "scam", "骗子", "垃圾"))


def _active_workflow_conflict_has_data(state: GraphState, active_workflow: str) -> bool:
    if _is_independent_faq_during_workflow(state):
        return False
    if not _is_cross_workflow_business_object(state, active_workflow):
        return False
    slot_memory = state.get("slot_memory") or {}
    if state.get("attachments"):
        return True
    return any(
        slot_memory.get(key)
        for key in (
            "account_or_phone",
            "phone",
            "amount",
            "order_id",
            "payment_channel",
            "deposit_screenshot",
            "withdrawal_screenshot",
            "receipt_screenshot",
            "telegram_case_id",
            "telegram_message_id",
        )
    )


def _increment_slot_counter(state: GraphState, key: str) -> GraphState:
    slot_memory = dict(state.get("slot_memory") or {})
    counters = dict(slot_memory.get("handoff_counters") or {})
    counters[key] = int(counters.get(key) or 0) + 1
    slot_memory["handoff_counters"] = counters
    return {**state, "slot_memory": slot_memory}


def _slot_counter(state: GraphState, key: str) -> int:
    return int(((state.get("slot_memory") or {}).get("handoff_counters") or {}).get(key) or 0)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)
