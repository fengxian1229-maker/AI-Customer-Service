from app_v2.graph.state import GraphState


def record_node(state: GraphState, node_name: str) -> dict:
    path = [*state.get("trace_context", {}).get("node_path", []), node_name]
    return {"trace_context": {"node_path": path}}
