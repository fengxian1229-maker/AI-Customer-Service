from typing import Any

from app.graph.state import GraphState
from app.schemas.events import InboundEvent
from app.workflows.command_contracts import CommandType
from app.services.rag import answer_from_rag_context, answer_from_static_knowledge
from app.services.language_policy import detect_language_deterministic
from app.workflows.slot_extractors import (
    attachment_urls,
    extract_amount,
    extract_identity,
    is_explicit_human_request,
    normalize_text,
)
from app.workflows.sop_handlers import run_sop
from app.workflows.waiting_backend_classifier import handle_waiting_backend
from app.workflows.final_reply_policy import build_reply_plan


LLM_AUTHORITATIVE_SOURCES = {
    "llm_guarded_authoritative",
    "llm_guarded_authoritative_post_guard",
    "llm_faq_authoritative",
}


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

    if active and stage in {"waiting_backend", "backend_querying", "collecting_slots", "lookup_pending_reply"}:
        return {
            **state,
            "intent_result": _intent_result(
                intent=active,
                route="sop",
                reason="Continue active workflow through SOP handler.",
                confidence=0.95,
                sop_name=active,
            ),
            "route": "sop",
        }

    if hints["has_explicit_human_request"]:
        return _with_route(state, "explicit_human_request", "human_handoff", "Customer explicitly requested a human agent.")
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
    if text:
        return _with_route(state, "clarification_needed", "clarification", "Question is outside canonical FAQ targets.", confidence=0.55)
    return _with_route(state, "clarification_needed", "clarification", "No clear question content provided.", confidence=0.2)


def sop_node(state: GraphState) -> GraphState:
    if state.get("workflow_stage") in {"waiting_backend", "backend_querying"}:
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


def human_handoff_node(state: GraphState) -> GraphState:
    intent = (state.get("intent_result") or {}).get("intent")
    reason = intent or "explicit_human_request"
    return {
        **state,
        "status": "HANDOFF_REQUESTED",
        "active_workflow": "human_handoff",
        "workflow_stage": "handoff_requested",
        "response_text": "我会为你转接真人客服继续协助。",
        "response_text_fallback": "我会为你转接真人客服继续协助。",
        "reply_plan": build_reply_plan(
            kind="human_handoff",
            fallback_text="我会为你转接真人客服继续协助。",
            must_say=["转接真人客服"],
            semantic_required_items=["human_handoff_notice"],
            must_not_say=["已接入", "马上处理", "已处理", "已到账", "已完成"],
            allowed_facts=["客户需要真人客服", "系统将提出转接请求"],
        ),
        "commands": [{"type": CommandType.HUMAN_HANDOFF_REQUESTED, "payload": {"reason": reason}}],
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
        commands.insert(
            0,
            {
                "type": CommandType.LIVECHAT_SEND_TEXT,
                "payload": {"text": text},
            },
        )
    return {**state, "commands": commands}


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
        ),
        "route": route,
    }


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
