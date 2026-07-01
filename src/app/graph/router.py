from typing import Literal

from app.graph.state import GraphState


def route_condition(state: GraphState) -> Literal[
    "sop",
    "rag",
    "emotion_care",
    "human_handoff",
    "contextual_reply",
    "casual_chat",
    "clarification",
]:
    route = state.get("route")
    if route == "faq":
        return "rag"
    if route == "faq_then_sop":
        return "sop"
    if route in {"sop", "emotion_care", "human_handoff", "contextual_reply", "casual_chat", "clarification"}:
        return route
    return "clarification"
