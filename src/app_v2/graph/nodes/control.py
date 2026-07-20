from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def prepare_direct_reply_node(state: GraphState) -> dict:
    return record_node(state, "prepare_direct_reply")


def prepare_handoff_node(state: GraphState) -> dict:
    return record_node(state, "prepare_handoff")


def prepare_clarification_node(state: GraphState) -> dict:
    return record_node(state, "prepare_clarification")


def apply_control_result_node(state: GraphState) -> dict:
    update = record_node(state, "apply_control_result")
    outcome = state.get("execution", {}).get("outcome", "CONTROL_REPLY_REQUIRED")
    update["execution"] = {**state.get("execution", {}), "outcome": outcome}
    return update


def prepare_control_reply_node(state: GraphState) -> dict:
    return record_node(state, "prepare_control_reply")


def apply_conversation_status_update_node(state: GraphState) -> dict:
    return record_node(state, "apply_conversation_status_update")
