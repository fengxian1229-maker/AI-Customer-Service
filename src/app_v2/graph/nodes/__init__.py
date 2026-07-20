"""Independently testable V2 Graph nodes."""

from app_v2.graph.nodes.context import (
    build_deferred_message_batch_node,
    load_deferred_messages_node,
    load_event_context_node,
)
from app_v2.graph.nodes.multimodal import analyze_multimodal_content_node
from app_v2.graph.nodes.control import (
    apply_control_result_node,
    apply_conversation_status_update_node,
    prepare_clarification_node,
    prepare_control_reply_node,
    prepare_direct_reply_node,
    prepare_handoff_node,
)
from app_v2.graph.nodes.knowledge import knowledge_node
from app_v2.graph.nodes.persistence import persist_result_node
from app_v2.graph.nodes.reply import compose_reply_node, fact_guard_node
from app_v2.graph.nodes.understanding import classify_intent_node, interpret_workflow_node, normalize_turn_node
from app_v2.graph.nodes.workflow import (
    apply_capability_result_node,
    prepare_query_pending_reply_node,
    prepare_result_reply_node,
    verify_job_still_pending_node,
    workflow_engine_node,
)


NODE_REGISTRY = {
    "load_event_context": load_event_context_node,
    "analyze_multimodal_content": analyze_multimodal_content_node,
    "normalize_turn": normalize_turn_node,
    "classify_intent": classify_intent_node,
    "interpret_workflow": interpret_workflow_node,
    "knowledge": knowledge_node,
    "workflow_engine": workflow_engine_node,
    "prepare_handoff": prepare_handoff_node,
    "prepare_direct_reply": prepare_direct_reply_node,
    "prepare_clarification": prepare_clarification_node,
    "compose_reply": compose_reply_node,
    "fact_guard": fact_guard_node,
    "persist_result": persist_result_node,
    "apply_capability_result": apply_capability_result_node,
    "prepare_result_reply": prepare_result_reply_node,
    "verify_job_still_pending": verify_job_still_pending_node,
    "prepare_query_pending_reply": prepare_query_pending_reply_node,
    "apply_control_result": apply_control_result_node,
    "prepare_control_reply": prepare_control_reply_node,
    "apply_conversation_status_update": apply_conversation_status_update_node,
    "load_deferred_messages": load_deferred_messages_node,
    "build_deferred_message_batch": build_deferred_message_batch_node,
}
