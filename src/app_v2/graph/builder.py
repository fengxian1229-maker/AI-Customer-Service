from langgraph.graph import END, START, StateGraph

from app_v2.graph.nodes.multimodal import analyze_multimodal_content_node
from app_v2.graph.nodes.context import load_event_context_node
from app_v2.graph.nodes.control import prepare_clarification_node, prepare_direct_reply_node, prepare_handoff_node
from app_v2.graph.nodes.knowledge import knowledge_node
from app_v2.graph.nodes.persistence import persist_result_node
from app_v2.graph.nodes.reply import compose_reply_node, fact_guard_node
from app_v2.graph.nodes.understanding import classify_intent_node, interpret_workflow_node, normalize_turn_node
from app_v2.graph.nodes.workflow import workflow_engine_node
from app_v2.graph.routing import (
    dispatch_intent_router,
    entry_router,
    multimodal_content_router,
    workflow_relation_router,
)
from app_v2.graph.state import GraphState


def build_user_message_graph():
    graph = StateGraph(GraphState)
    graph.add_node("load_event_context", load_event_context_node)
    graph.add_node("analyze_multimodal_content", analyze_multimodal_content_node)
    graph.add_node("normalize_turn", normalize_turn_node)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("interpret_workflow", interpret_workflow_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("workflow_engine", workflow_engine_node)
    graph.add_node("prepare_handoff", prepare_handoff_node)
    graph.add_node("prepare_direct_reply", prepare_direct_reply_node)
    graph.add_node("prepare_clarification", prepare_clarification_node)
    graph.add_node("compose_reply", compose_reply_node)
    graph.add_node("fact_guard", fact_guard_node)
    graph.add_node("persist_result", persist_result_node)

    graph.add_edge(START, "load_event_context")
    graph.add_conditional_edges(
        "load_event_context",
        multimodal_content_router,
        {"analyze_multimodal_content": "analyze_multimodal_content", "normalize_turn": "normalize_turn"},
    )
    graph.add_edge("analyze_multimodal_content", "normalize_turn")
    graph.add_conditional_edges(
        "normalize_turn",
        entry_router,
        {"classify_intent": "classify_intent", "interpret_workflow": "interpret_workflow"},
    )
    dispatch_routes = {
        "knowledge": "knowledge",
        "workflow_engine": "workflow_engine",
        "prepare_handoff": "prepare_handoff",
        "prepare_direct_reply": "prepare_direct_reply",
        "prepare_clarification": "prepare_clarification",
    }
    graph.add_conditional_edges("classify_intent", dispatch_intent_router, dispatch_routes)
    graph.add_conditional_edges(
        "interpret_workflow",
        workflow_relation_router,
        {
            "classify_intent": "classify_intent",
            "workflow_engine": "workflow_engine",
            "prepare_handoff": "prepare_handoff",
            "prepare_direct_reply": "prepare_direct_reply",
        },
    )
    for reply_source in (
        "knowledge",
        "workflow_engine",
        "prepare_handoff",
        "prepare_direct_reply",
        "prepare_clarification",
    ):
        graph.add_edge(reply_source, "compose_reply")
    graph.add_edge("compose_reply", "fact_guard")
    graph.add_edge("fact_guard", "persist_result")
    graph.add_edge("persist_result", END)
    return graph.compile()
