from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def compose_reply_node(state: GraphState) -> dict:
    return record_node(state, "compose_reply")


def fact_guard_node(state: GraphState) -> dict:
    return record_node(state, "fact_guard")
