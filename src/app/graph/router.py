from typing import Literal

from app.graph.state import GraphState


def route_condition(state: GraphState) -> Literal[
    "sop",
    "rag",
    "emotion_care",
    "human_handoff",
    "final_reply",
]:
    route = state.get("route")
    if route == "faq":
        return "rag"
    if route in {"sop", "emotion_care", "human_handoff", "final_reply"}:
        return route
    return "final_reply"
