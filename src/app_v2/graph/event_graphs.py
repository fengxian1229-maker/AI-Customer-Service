from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app_v2.graph.builder import build_user_message_graph
from app_v2.graph.nodes.context import (
    build_deferred_message_batch_node,
    load_deferred_messages_node,
    load_event_context_node,
)
from app_v2.graph.nodes.control import (
    apply_control_result_node,
    apply_conversation_status_update_node,
    prepare_control_reply_node,
)
from app_v2.graph.nodes.persistence import persist_result_node
from app_v2.graph.nodes.reply import compose_reply_node, fact_guard_node
from app_v2.graph.nodes.workflow import (
    apply_capability_result_node,
    prepare_query_pending_reply_node,
    prepare_result_reply_node,
    verify_job_still_pending_node,
)
from app_v2.graph.routing import control_reply_router, pending_notice_router
from app_v2.graph.state import GraphState


def build_capability_result_graph():
    graph = StateGraph(GraphState)
    _add_nodes(
        graph,
        load_event_context=load_event_context_node,
        apply_capability_result=apply_capability_result_node,
        prepare_result_reply=prepare_result_reply_node,
        compose_reply=compose_reply_node,
        fact_guard=fact_guard_node,
        persist_result=persist_result_node,
    )
    _chain(graph, START, "load_event_context", "apply_capability_result", "prepare_result_reply", "compose_reply", "fact_guard", "persist_result", END)
    return graph.compile()


def build_capability_pending_graph():
    graph = StateGraph(GraphState)
    _add_nodes(
        graph,
        load_event_context=load_event_context_node,
        verify_job_still_pending=verify_job_still_pending_node,
        prepare_query_pending_reply=prepare_query_pending_reply_node,
        compose_reply=compose_reply_node,
        fact_guard=fact_guard_node,
        persist_result=persist_result_node,
    )
    _chain(graph, START, "load_event_context", "verify_job_still_pending")
    graph.add_conditional_edges(
        "verify_job_still_pending",
        pending_notice_router,
        {"prepare_query_pending_reply": "prepare_query_pending_reply", "persist_result": "persist_result"},
    )
    _chain(graph, "prepare_query_pending_reply", "compose_reply", "fact_guard", "persist_result", END)
    return graph.compile()


def build_control_result_graph():
    graph = StateGraph(GraphState)
    _add_nodes(
        graph,
        load_event_context=load_event_context_node,
        apply_control_result=apply_control_result_node,
        prepare_control_reply=prepare_control_reply_node,
        compose_reply=compose_reply_node,
        fact_guard=fact_guard_node,
        persist_result=persist_result_node,
    )
    _chain(graph, START, "load_event_context", "apply_control_result")
    graph.add_conditional_edges(
        "apply_control_result",
        control_reply_router,
        {"prepare_control_reply": "prepare_control_reply", "persist_result": "persist_result"},
    )
    _chain(graph, "prepare_control_reply", "compose_reply", "fact_guard", "persist_result", END)
    return graph.compile()


def build_conversation_status_graph():
    return _build_linear_graph("apply_conversation_status_update", apply_conversation_status_update_node)


def build_deferred_resume_graph():
    graph = StateGraph(GraphState)
    _add_nodes(
        graph,
        load_event_context=load_event_context_node,
        load_deferred_messages=load_deferred_messages_node,
        build_deferred_message_batch=build_deferred_message_batch_node,
        user_message_graph=build_user_message_graph(),
    )
    _chain(graph, START, "load_event_context", "load_deferred_messages", "build_deferred_message_batch", "user_message_graph", END)
    return graph.compile()


EVENT_GRAPH_BUILDERS: dict[str, Callable[[], object]] = {
    "UserMessage": build_user_message_graph,
    "CapabilityResultEvent": build_capability_result_graph,
    "CapabilityPendingDueEvent": build_capability_pending_graph,
    "ControlDirectiveResult": build_control_result_graph,
    "ConversationStatusUpdate": build_conversation_status_graph,
    "ResumeDeferredMessagesEvent": build_deferred_resume_graph,
}


def build_event_graph(event_type: str):
    try:
        builder = EVENT_GRAPH_BUILDERS[event_type]
    except KeyError as exc:
        raise ValueError(f"No business Graph registered for event type: {event_type}") from exc
    return builder()


def _build_linear_graph(node_name: str, node: Callable):
    graph = StateGraph(GraphState)
    _add_nodes(graph, load_event_context=load_event_context_node, **{node_name: node}, persist_result=persist_result_node)
    _chain(graph, START, "load_event_context", node_name, "persist_result", END)
    return graph.compile()


def _add_nodes(graph: StateGraph, **nodes: Callable) -> None:
    for name, node in nodes.items():
        graph.add_node(name, node)


def _chain(graph: StateGraph, *nodes: str) -> None:
    for source, target in zip(nodes, nodes[1:]):
        graph.add_edge(source, target)
