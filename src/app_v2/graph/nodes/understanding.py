from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def normalize_turn_node(state: GraphState) -> dict:
    return record_node(state, "normalize_turn")


def classify_intent_node(state: GraphState) -> dict:
    update = record_node(state, "classify_intent")
    update["understanding"] = {**state.get("understanding", {}), "intent": state.get("understanding", {}).get("intent", "casual_chat")}
    return update


def interpret_workflow_node(state: GraphState) -> dict:
    update = record_node(state, "interpret_workflow")
    update["understanding"] = {
        **state.get("understanding", {}),
        "workflow_relation": state.get("understanding", {}).get("workflow_relation", "acknowledgement"),
    }
    return update
