from typing import Literal

from app_v2.graph.state import GraphState
from app_v2.runtime.intents import INTENT_HANDLERS


def multimodal_content_router(state: GraphState) -> Literal["analyze_multimodal_content", "normalize_turn"]:
    return "analyze_multimodal_content" if state.get("event", {}).get("has_multimodal_content") else "normalize_turn"


def entry_router(state: GraphState) -> Literal["classify_intent", "interpret_workflow"]:
    return "interpret_workflow" if state["session"].active_workflow else "classify_intent"


def dispatch_intent_router(
    state: GraphState,
) -> Literal["knowledge", "workflow_engine", "prepare_handoff", "prepare_direct_reply", "prepare_clarification"]:
    intent = state.get("understanding", {}).get("intent")
    handler = INTENT_HANDLERS.get(intent or "", "clarification")
    return {
        "knowledge": "knowledge",
        "workflow": "workflow_engine",
        "handoff": "prepare_handoff",
        "direct_reply": "prepare_direct_reply",
        "clarification": "prepare_clarification",
    }[handler]


def workflow_relation_router(
    state: GraphState,
) -> Literal["classify_intent", "workflow_engine", "prepare_handoff", "prepare_direct_reply"]:
    relation = state.get("understanding", {}).get("workflow_relation")
    if relation in {"independent_faq", "switch_topic"}:
        return "classify_intent"
    if relation == "human_request":
        return "prepare_handoff"
    if relation == "resolved_or_cancel":
        return "prepare_direct_reply"
    return "workflow_engine"


def pending_notice_router(state: GraphState) -> Literal["prepare_query_pending_reply", "persist_result"]:
    return (
        "prepare_query_pending_reply"
        if state.get("execution", {}).get("outcome") == "JOB_STILL_PENDING"
        else "persist_result"
    )


def control_reply_router(state: GraphState) -> Literal["prepare_control_reply", "persist_result"]:
    return (
        "prepare_control_reply"
        if state.get("execution", {}).get("outcome") == "CONTROL_REPLY_REQUIRED"
        else "persist_result"
    )
