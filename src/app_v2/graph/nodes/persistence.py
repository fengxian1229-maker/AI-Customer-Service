from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def persist_result_node(state: GraphState) -> dict:
    return record_node(state, "persist_result")
