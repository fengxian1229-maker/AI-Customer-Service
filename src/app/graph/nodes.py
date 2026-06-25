from typing import Any

from app.graph.state import GraphState
from app.schemas.events import InboundEvent
from app.workflows.command_contracts import CommandType
from app.workflows.slot_extractors import (
    extract_amount,
    extract_identity,
    extract_transaction_signal,
    is_explicit_human_request,
    normalize_text,
)
from app.workflows.sop_handlers import run_sop
from app.workflows.waiting_backend_classifier import handle_waiting_backend


def build_graph_state_from_event(event: InboundEvent, conversation: dict[str, Any]) -> GraphState:
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
        "event_type": event.standard_event_type,
        "attachments": _extract_attachments(payload, event.standard_event_type),
        "status": conversation.get("status") or "AI_ACTIVE",
        "active_workflow": conversation.get("active_workflow"),
        "workflow_stage": conversation.get("workflow_stage"),
        "slot_memory": dict(conversation.get("slot_memory") or {}),
        "signal_result": None,
        "intent_result": None,
        "route": None,
        "response_text": None,
        "commands": [],
        "errors": [],
    }


def rewrite_question_node(state: GraphState) -> GraphState:
    raw = normalize_text(state.get("raw_user_input"))
    identity = extract_identity(raw)
    return {
        **state,
        "rewritten_question": raw,
        "rewrite_result": {
            "rewritten_question": raw,
            "language": _detect_language(raw),
            "mentioned_entities": {
                "account_or_phone": identity["value"] if identity else None,
                "transaction_ref": None,
                "amount": extract_amount(raw),
                "date": None,
            },
            "notes": [],
        },
    }


def signal_judgement_node(state: GraphState) -> GraphState:
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
    identity = extract_identity(text)
    transaction = extract_transaction_signal(text)
    has_attachment = bool(state.get("attachments"))
    lower = text.lower()
    has_deposit = any(token in lower for token in ("deposit", "depósito", "deposito", "recarga", "充值", "存款"))
    has_withdrawal = any(token in lower for token in ("withdrawal", "retiro", "retirar", "提款", "提现"))
    has_missing = any(token in lower for token in ("no llegó", "no llego", "no recibido", "no acreditado", "未到账", "没到账"))
    has_blocked = any(token in lower for token in ("no puedo retirar", "无法提款", "流水", "rollover", "限制"))

    signal = {
        "has_identity": bool(identity),
        "identity_type": identity["type"] if identity else None,
        "identity_value": identity["value"] if identity else None,
        "has_attachment": has_attachment,
        "attachment_count": len(state.get("attachments", [])),
        "has_transaction_signal": bool(transaction),
        "transaction_signal_type": transaction["type"] if transaction else None,
        "transaction_signal_value": transaction["value"] if transaction else None,
        "has_explicit_human_request": is_explicit_human_request(text),
        "has_deposit_signal": has_deposit,
        "has_withdrawal_signal": has_withdrawal,
        "has_withdrawal_missing_signal": has_withdrawal and has_missing,
        "has_withdrawal_blocked_signal": has_withdrawal and has_blocked,
        "has_deposit_missing_signal": has_deposit and has_missing,
        "has_password_signal": any(token in lower for token in ("contraseña", "password", "忘记密码")),
        "has_pending_reply_signal": any(token in lower for token in ("caso anterior", "previous case", "上一笔案件")),
        "risk_level": "normal",
        "confidence": 0.85 if any([identity, transaction, has_attachment, has_deposit, has_withdrawal]) else 0.3,
    }
    return {**state, "signal_result": signal}


def intent_router_node(state: GraphState) -> GraphState:
    signal = state.get("signal_result") or {}
    active = state.get("active_workflow")
    stage = state.get("workflow_stage")
    text = normalize_text(state.get("rewritten_question") or state.get("raw_user_input")).lower()

    if active and stage in {"waiting_backend", "backend_querying"}:
        intent = "waiting_backend_supplement" if _has_waiting_supplement(signal, state) else "waiting_backend_followup"
        if signal.get("has_explicit_human_request") and not _has_waiting_supplement(signal, state):
            intent = "human_handoff"
        result = _intent_result(intent, reason="Continue active waiting backend workflow.", continue_workflow=True)
        return {**state, "intent_result": result, "route": "continue_workflow" if intent != "human_handoff" else "human_handoff"}

    if active and stage == "collecting_slots":
        result = _intent_result(active, reason="Continue active slot collection workflow.", continue_workflow=True)
        return {**state, "intent_result": result, "route": "sop"}

    if signal.get("has_explicit_human_request"):
        intent = "human_handoff"
    elif signal.get("has_withdrawal_blocked_signal"):
        intent = "withdrawal_blocked_or_rollover"
    elif signal.get("has_withdrawal_missing_signal"):
        intent = "withdrawal_missing"
    elif signal.get("has_deposit_missing_signal"):
        intent = "deposit_missing"
    elif signal.get("has_password_signal"):
        intent = "forgot_password"
    elif signal.get("has_pending_reply_signal"):
        intent = "pending_reply_lookup"
    elif any(token in text for token in ("cómo recargar", "como recargar", "如何充值", "how to deposit")):
        intent = "deposit_howto"
    elif any(token in text for token in ("cómo retirar", "como retirar", "如何提款", "how to withdraw")):
        intent = "withdrawal_howto"
    elif state.get("event_type") == "FILE_RECEIVED":
        intent = "unknown"
    elif text:
        intent = "faq_general"
    else:
        intent = "unknown"

    route = "human_handoff" if intent == "human_handoff" else "rag" if intent == "faq_general" else "sop" if intent not in {"unknown"} else "clarification"
    return {**state, "intent_result": _intent_result(intent), "route": route}


def continue_workflow_node(state: GraphState) -> GraphState:
    if state.get("workflow_stage") in {"waiting_backend", "backend_querying"}:
        return handle_waiting_backend(state)
    return run_sop(state)


def sop_node(state: GraphState) -> GraphState:
    return run_sop(state)


def rag_placeholder_node(state: GraphState) -> GraphState:
    return {
        **state,
        "response_text": "这个问题我先记录下来，目前知识库仍在接入中，请补充更具体的问题或选择真人客服。",
        "commands": [{"type": CommandType.RAG_PLACEHOLDER, "payload": {"intent": "faq_general"}}],
    }


def human_handoff_node(state: GraphState) -> GraphState:
    return {
        **state,
        "status": "HANDOFF_REQUESTED",
        "active_workflow": "human_handoff",
        "workflow_stage": "handoff_requested",
        "response_text": "我会为你转接真人客服继续协助。",
        "commands": [{"type": CommandType.HUMAN_HANDOFF_REQUESTED, "payload": {"reason": "explicit_human_request"}}],
    }


def clarification_node(state: GraphState) -> GraphState:
    return {**state, "response_text": "请补充你要咨询的问题，或说明是存款、提款、流水还是需要真人客服。", "commands": []}


def command_planner_node(state: GraphState) -> GraphState:
    commands = list(state.get("commands") or [])
    if state.get("response_text"):
        commands.insert(
            0,
            {
                "type": CommandType.LIVECHAT_SEND_TEXT,
                "payload": {"text": state["response_text"]},
            },
        )
    return {**state, "commands": commands}


def persist_state_node(state: GraphState) -> GraphState:
    return state


def _intent_result(intent: str, reason: str | None = None, continue_workflow: bool = False) -> dict[str, Any]:
    return {
        "intent": intent,
        "confidence": 0.9 if intent != "unknown" else 0.2,
        "reason": reason or "Deterministic first-pass routing.",
        "should_continue_active_workflow": continue_workflow,
        "requires_sop": intent
        in {
            "deposit_missing",
            "withdrawal_missing",
            "withdrawal_blocked_or_rollover",
            "deposit_howto",
            "withdrawal_howto",
            "forgot_password",
            "pending_reply_lookup",
        },
        "requires_rag": intent == "faq_general",
        "requires_human": intent == "human_handoff",
        "requires_backend": intent == "withdrawal_blocked_or_rollover",
        "requires_tg": intent in {"deposit_missing", "withdrawal_missing"},
    }


def _has_waiting_supplement(signal: dict[str, Any], state: GraphState) -> bool:
    return bool(state.get("attachments") or signal.get("has_transaction_signal") or signal.get("has_identity"))


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
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "zh"
    if any(token in text.lower() for token in ("depósito", "retiro", "contraseña", "usuario")):
        return "es"
    if text:
        return "unknown"
    return "unknown"
