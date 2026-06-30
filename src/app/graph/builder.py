from langgraph.graph import END, StateGraph

from app.graph.nodes import (
    clarification_node,
    command_planner_node,
    emotion_care_node,
    human_handoff_node,
    intent_router_node,
    make_final_reply_node,
    make_intent_router_node,
    make_language_policy_node,
    make_rag_node,
    make_rewrite_question_node,
    make_sop_node,
    persist_state_node,
    rag_node,
    rewrite_question_node,
    sop_node,
)
from app.graph.router import route_condition
from app.graph.state import GraphState


def build_workflow_graph(
    *,
    checkpointer=None,
    llm_rewrite_service=None,
    llm_intent_service=None,
    llm_sop_slot_service=None,
    final_reply_service=None,
    rag_service=None,
    language_detection_enabled: bool = True,
    language_detection_min_confidence: float = 0.70,
    tenant_persona_default_language: str = "zh-Hans",
    tenant_supported_languages: str | list[str] = "zh-Hans,zh-Hant,en,es,tl,th,my,ms",
    language_fallback: str = "zh-Hans",
    language_persist_to_slot_memory: bool = True,
    llm_rewrite_min_confidence: float = 0.70,
    llm_router_mode: str = "shadow",
    llm_router_min_confidence: float = 0.70,
    llm_router_fallback_to_deterministic: bool = True,
    llm_sop_slot_enabled: bool = False,
    llm_sop_slot_min_confidence: float = 0.70,
    llm_sop_slot_fallback_to_deterministic: bool = True,
    llm_final_reply_enabled: bool = False,
):
    graph = StateGraph(GraphState)
    graph.add_node(
        "rewrite_question_node",
        make_rewrite_question_node(llm_rewrite_service, min_confidence=llm_rewrite_min_confidence),
    )
    graph.add_node(
        "language_policy_node",
        make_language_policy_node(
            language_detection_enabled=language_detection_enabled,
            language_detection_min_confidence=language_detection_min_confidence,
            tenant_persona_default_language=tenant_persona_default_language,
            tenant_supported_languages=tenant_supported_languages,
            language_fallback=language_fallback,
            language_persist_to_slot_memory=language_persist_to_slot_memory,
        ),
    )
    graph.add_node(
        "intent_router_node",
        make_intent_router_node(
            llm_intent_service,
            llm_router_mode=llm_router_mode,
            llm_router_min_confidence=llm_router_min_confidence,
            llm_router_fallback_to_deterministic=llm_router_fallback_to_deterministic,
        ),
    )
    graph.add_node(
        "sop_node",
        make_sop_node(
            llm_sop_slot_service,
            llm_sop_slot_enabled=llm_sop_slot_enabled,
            llm_sop_slot_min_confidence=llm_sop_slot_min_confidence,
        ),
    )
    graph.add_node("rag_node", make_rag_node(rag_service))
    graph.add_node("emotion_care_node", emotion_care_node)
    graph.add_node("human_handoff_node", human_handoff_node)
    graph.add_node("clarification_node", clarification_node)
    graph.add_node(
        "final_reply_node",
        make_final_reply_node(
            final_reply_service if llm_final_reply_enabled else None,
            llm_final_reply_enabled=llm_final_reply_enabled,
        ),
    )
    graph.add_node("command_planner_node", command_planner_node)
    graph.add_node("persist_state_node", persist_state_node)

    graph.set_entry_point("rewrite_question_node")
    graph.add_edge("rewrite_question_node", "language_policy_node")
    graph.add_edge("language_policy_node", "intent_router_node")
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
    for node in ("sop_node", "rag_node", "emotion_care_node", "human_handoff_node", "clarification_node"):
        graph.add_edge(node, "final_reply_node")
    graph.add_edge("final_reply_node", "command_planner_node")
    graph.add_edge("command_planner_node", "persist_state_node")
    graph.add_edge("persist_state_node", END)
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()
