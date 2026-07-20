from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def workflow_engine_node(state: GraphState) -> dict:
    return record_node(state, "workflow_engine")


def apply_capability_result_node(state: GraphState) -> dict:
    return record_node(state, "apply_capability_result")


def prepare_result_reply_node(state: GraphState) -> dict:
    return record_node(state, "prepare_result_reply")


def verify_job_still_pending_node(state: GraphState) -> dict:
    update = record_node(state, "verify_job_still_pending")
    outcome = state.get("execution", {}).get("outcome", "JOB_STILL_PENDING")
    update["execution"] = {**state.get("execution", {}), "outcome": outcome}
    return update


def prepare_query_pending_reply_node(state: GraphState) -> dict:
    return record_node(state, "prepare_query_pending_reply")
