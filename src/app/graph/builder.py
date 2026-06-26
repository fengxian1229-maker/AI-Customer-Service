from langgraph.graph import END, StateGraph

from app.graph.nodes import (
    clarification_node,
    command_planner_node,
    continue_workflow_node,
    emotion_care_node,
    human_handoff_node,
    intent_router_node,
    persist_state_node,
    rag_node,
    rewrite_question_node,
    signal_judgement_node,
    sop_node,
)
from app.graph.router import route_condition
from app.graph.state import GraphState


def build_workflow_graph(checkpointer=None):
    graph = StateGraph(GraphState)
    graph.add_node("rewrite_question_node", rewrite_question_node)
    graph.add_node("signal_judgement_node", signal_judgement_node)
    graph.add_node("intent_router_node", intent_router_node)
    graph.add_node("continue_workflow_node", continue_workflow_node)
    graph.add_node("sop_node", sop_node)
    graph.add_node("rag_node", rag_node)
    graph.add_node("emotion_care_node", emotion_care_node)
    graph.add_node("human_handoff_node", human_handoff_node)
    graph.add_node("clarification_node", clarification_node)
    graph.add_node("command_planner_node", command_planner_node)
    graph.add_node("persist_state_node", persist_state_node)

    graph.set_entry_point("rewrite_question_node")
    graph.add_edge("rewrite_question_node", "signal_judgement_node")
    graph.add_edge("signal_judgement_node", "intent_router_node")
    graph.add_conditional_edges(
        "intent_router_node",
        route_condition,
        {
            "sop": "sop_node",
            "rag": "rag_node",
            "emotion_care": "emotion_care_node",
            "human_handoff": "human_handoff_node",
            "clarification": "clarification_node",
        },
    )
    for node in ("continue_workflow_node", "sop_node", "rag_node", "emotion_care_node", "human_handoff_node", "clarification_node"):
        graph.add_edge(node, "command_planner_node")
    graph.add_edge("command_planner_node", "persist_state_node")
    graph.add_edge("persist_state_node", END)
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()
