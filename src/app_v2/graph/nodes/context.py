from app_v2.graph.nodes._trace import record_node
from app_v2.graph.state import GraphState


def load_event_context_node(state: GraphState) -> dict:
    return record_node(state, "load_event_context")


def load_deferred_messages_node(state: GraphState) -> dict:
    return record_node(state, "load_deferred_messages")


def build_deferred_message_batch_node(state: GraphState) -> dict:
    return record_node(state, "build_deferred_message_batch")
