from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def analyze_multimodal_content_node(state: GraphState) -> dict:
    return record_node(state, "analyze_multimodal_content")
