from typing import Literal

from app.graph.state import GraphState


def route_condition(state: GraphState) -> Literal["continue_workflow", "sop", "rag", "human_handoff", "clarification"]:
    route = state.get("route")
    if route in {"continue_workflow", "sop", "rag", "human_handoff", "clarification"}:
        return route
    return "clarification"
