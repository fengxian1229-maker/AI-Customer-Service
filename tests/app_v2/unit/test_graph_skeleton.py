from datetime import UTC, datetime

import pytest

from app_v2.domain.session import ClarificationState, ServiceStatus, SessionState
from app_v2.domain.workflow import WorkflowInstance
from app_v2.graph.builder import build_user_message_graph
from app_v2.graph.event_graphs import build_event_graph
from app_v2.runtime.registry import RuntimeProfile


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def session_state() -> SessionState:
    return SessionState(
        schema_version=1,
        tenant_id="tenant-1",
        agent_id="agent-1",
        conversation_id="conversation-1",
        version=0,
        runtime_version="runtime-v1",
        service_status=ServiceStatus.BOT_ACTIVE,
        conversation_language="en",
        clarification=ClarificationState(),
        handoff=None,
        created_at=NOW,
        updated_at=NOW,
    )


def graph_state() -> dict:
    return {
        "event": {"event_type": "UserMessage", "has_multimodal_content": False},
        "session": session_state(),
        "runtime": RuntimeProfile(
            agent_id="agent-1",
            tenant_id="tenant-1",
            runtime_version="runtime-v1",
        ),
        "understanding": {"intent": "casual_chat"},
        "execution": {},
        "reply": {},
        "trace_context": {"node_path": []},
    }


def test_user_message_graph_without_active_workflow_runs_intent_compose_and_persist():
    result = build_user_message_graph().invoke(graph_state())

    assert result["trace_context"]["node_path"] == [
        "load_event_context",
        "normalize_turn",
        "classify_intent",
        "prepare_direct_reply",
        "compose_reply",
        "fact_guard",
        "persist_result",
    ]


def test_user_message_graph_analyzes_multimodal_content_before_normalization():
    state = graph_state()
    state["event"]["has_multimodal_content"] = True

    result = build_user_message_graph().invoke(state)

    assert result["trace_context"]["node_path"][:3] == [
        "load_event_context",
        "analyze_multimodal_content",
        "normalize_turn",
    ]


@pytest.mark.parametrize(
    ("intent", "handler"),
    [
        ("deposit_howto", "knowledge"),
        ("turnover_requirement_query", "workflow_engine"),
        ("explicit_human_request", "prepare_handoff"),
        ("casual_chat", "prepare_direct_reply"),
        ("clarification_needed", "prepare_clarification"),
    ],
)
def test_user_message_graph_dispatches_each_intent_family_through_compose(intent, handler):
    state = graph_state()
    state["understanding"]["intent"] = intent

    result = build_user_message_graph().invoke(state)

    path = result["trace_context"]["node_path"]
    assert handler in path
    assert "compose_reply" in path
    assert path[-1] == "persist_result"


@pytest.mark.parametrize(
    ("relation", "handler"),
    [
        ("supplement", "workflow_engine"),
        ("human_request", "prepare_handoff"),
        ("resolved_or_cancel", "prepare_direct_reply"),
    ],
)
def test_active_workflow_uses_workflow_interpretation_instead_of_entry_intent(relation, handler):
    state = graph_state()
    state["session"].active_workflow = WorkflowInstance(
        workflow_instance_id="workflow-1",
        workflow_name="turnover_requirement_query",
        slots={},
        started_at=NOW,
        updated_at=NOW,
    )
    state["understanding"]["workflow_relation"] = relation

    result = build_user_message_graph().invoke(state)

    path = result["trace_context"]["node_path"]
    assert "interpret_workflow" in path
    assert handler in path
    assert "classify_intent" not in path


def test_every_user_reply_runs_fact_guard_before_persist():
    result = build_user_message_graph().invoke(graph_state())

    assert result["trace_context"]["node_path"][-3:] == [
        "compose_reply",
        "fact_guard",
        "persist_result",
    ]


def test_handoff_notice_is_always_fact_guarded():
    state = graph_state()
    state["understanding"]["intent"] = "explicit_human_request"

    result = build_user_message_graph().invoke(state)

    assert result["trace_context"]["node_path"][-3:] == [
        "compose_reply",
        "fact_guard",
        "persist_result",
    ]


@pytest.mark.parametrize(
    ("event_type", "expected_path"),
    [
        (
            "CapabilityResultEvent",
            ["load_event_context", "apply_capability_result", "prepare_result_reply", "compose_reply", "fact_guard", "persist_result"],
        ),
        (
            "CapabilityPendingDueEvent",
            ["load_event_context", "verify_job_still_pending", "prepare_query_pending_reply", "compose_reply", "fact_guard", "persist_result"],
        ),
        (
            "ControlDirectiveResult",
            ["load_event_context", "apply_control_result", "prepare_control_reply", "compose_reply", "fact_guard", "persist_result"],
        ),
        (
            "ConversationStatusUpdate",
            ["load_event_context", "apply_conversation_status_update", "persist_result"],
        ),
        (
            "ResumeDeferredMessagesEvent",
            [
                "load_event_context",
                "load_deferred_messages",
                "build_deferred_message_batch",
                "load_event_context",
                "normalize_turn",
                "classify_intent",
                "prepare_direct_reply",
                "compose_reply",
                "fact_guard",
                "persist_result",
            ],
        ),
    ],
)
def test_system_event_graphs_compile_and_execute_complete_skeleton(event_type, expected_path):
    state = graph_state()
    state["event"]["event_type"] = event_type

    result = build_event_graph(event_type).invoke(state)

    assert result["trace_context"]["node_path"] == expected_path


@pytest.mark.parametrize(
    ("event_type", "outcome", "expected_path"),
    [
        (
            "CapabilityPendingDueEvent",
            "JOB_ALREADY_FINISHED",
            ["load_event_context", "verify_job_still_pending", "persist_result"],
        ),
        (
            "ControlDirectiveResult",
            "NO_CONTROL_REPLY",
            ["load_event_context", "apply_control_result", "persist_result"],
        ),
    ],
)
def test_system_event_graphs_route_from_one_execution_outcome(event_type, outcome, expected_path):
    state = graph_state()
    state["event"]["event_type"] = event_type
    state["execution"]["outcome"] = outcome

    result = build_event_graph(event_type).invoke(state)

    assert result["trace_context"]["node_path"] == expected_path


@pytest.mark.parametrize(
    "unsupported_event_type",
    ["AssistantMessage", "AssistantMessageSendResult", "IdleFollowupDueEvent", "IdleCloseDueEvent"],
)
def test_event_graph_registry_rejects_output_or_backend_owned_close_event_types(unsupported_event_type):
    with pytest.raises(ValueError):
        build_event_graph(unsupported_event_type)
