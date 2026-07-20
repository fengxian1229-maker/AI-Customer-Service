from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app_v2.domain.capability import CapabilityRequest
from app_v2.domain.directives import ControlDirective
from app_v2.domain.reply import ReplyPlan
from app_v2.domain.session import ClarificationState, HandoffState, ServiceStatus, SessionState
from app_v2.domain.workflow import SlotValue, WorkflowInstance


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def test_session_state_round_trip_preserves_workflow_and_control_state():
    session = SessionState(
        schema_version=1,
        tenant_id="tenant-1",
        agent_id="agent-1",
        conversation_id="conversation-1",
        version=3,
        runtime_version="runtime-v1",
        service_status=ServiceStatus.BOT_ACTIVE,
        conversation_language="en",
        clarification=ClarificationState(
            scope="workflow:workflow-1:collecting_slots:ambiguous_input",
            failure_count=1,
        ),
        active_workflow=WorkflowInstance(
            workflow_instance_id="workflow-1",
            workflow_name="turnover_requirement_query",
            slots={
                "account_or_phone": SlotValue(
                    value="user-123",
                    source_message_id="message-1",
                    source_type="user_text",
                )
            },
            started_at=NOW,
            updated_at=NOW,
        ),
        handoff=None,
        created_at=NOW,
        updated_at=NOW,
    )

    restored = SessionState.model_validate_json(session.model_dump_json())

    assert restored == session
    assert "history_snapshot" not in SessionState.model_fields
    assert "conversation_summary" not in SessionState.model_fields


@pytest.mark.parametrize(
    ("scope", "failure_count"),
    [(None, 1), ("intent:unrecognized", 0)],
)
def test_clarification_state_requires_scope_and_count_to_move_together(scope, failure_count):
    with pytest.raises(ValidationError):
        ClarificationState(scope=scope, failure_count=failure_count)


def test_handoff_state_contains_only_pending_directive_identity_and_time():
    handoff = HandoffState(directive_id="directive-1", requested_at=NOW)

    assert set(HandoffState.model_fields) == {"directive_id", "requested_at"}
    assert handoff.directive_id == "directive-1"


@pytest.mark.parametrize(
    ("service_status", "handoff"),
    [
        (ServiceStatus.HANDOFF_PENDING, None),
        (ServiceStatus.BOT_ACTIVE, HandoffState(directive_id="directive-1", requested_at=NOW)),
    ],
)
def test_session_requires_handoff_state_only_while_handoff_is_pending(service_status, handoff):
    with pytest.raises(ValidationError):
        SessionState(
            schema_version=1,
            tenant_id="tenant-1",
            agent_id="agent-1",
            conversation_id="conversation-1",
            version=0,
            runtime_version="runtime-v1",
            service_status=service_status,
            conversation_language="en",
            clarification=ClarificationState(),
            handoff=handoff,
            created_at=NOW,
            updated_at=NOW,
        )


def test_structured_effect_contracts_round_trip():
    capability = CapabilityRequest(
        query_request_id="query-1",
        workflow_instance_id="workflow-1",
        capability_id="backend_query",
        query_type="turnover_requirement",
        validated_payload={"account_or_phone": "user-123"},
    )
    reply = ReplyPlan(
        purpose="collect_account",
        response_kind="workflow_clarification",
        allowed_facts={"missing_slot": "account_or_phone"},
        required_facts=["missing_slot"],
        prohibited_claims=["query_completed"],
        related_event_ids=["evt-user-1"],
    )
    directive = ControlDirective(
        directive_id="directive-1",
        directive_type="handoff.requested",
        customer_notice="I am requesting a human agent.",
    )

    assert CapabilityRequest.model_validate_json(capability.model_dump_json()) == capability
    assert ReplyPlan.model_validate_json(reply.model_dump_json()) == reply
    assert ControlDirective.model_validate_json(directive.model_dump_json()) == directive


def test_effect_contracts_reject_removed_duplicate_idempotency_keys():
    with pytest.raises(ValidationError):
        CapabilityRequest(
            query_request_id="query-1",
            workflow_instance_id="workflow-1",
            capability_id="backend_query",
            query_type="turnover_requirement",
            validated_payload={"account_or_phone": "user-123"},
            idempotency_key="capability:query-1",
        )

    with pytest.raises(ValidationError):
        ControlDirective(
            directive_id="directive-1",
            directive_type="handoff.requested",
            customer_notice="I am requesting a human agent.",
            idempotency_key="handoff:directive-1",
        )


def test_control_directive_only_contains_backend_executable_fields():
    assert set(ControlDirective.model_fields) == {"directive_id", "directive_type", "customer_notice"}

    with pytest.raises(ValidationError):
        ControlDirective(
            directive_id="directive-1",
            directive_type="handoff.requested",
            customer_notice="",
        )


def test_workflow_state_uses_presence_slots_and_pending_query_instead_of_stage_status():
    assert set(WorkflowInstance.model_fields) == {
        "workflow_instance_id",
        "workflow_name",
        "slots",
        "pending_query_request_id",
        "started_at",
        "updated_at",
    }
    assert set(SlotValue.model_fields) == {"value", "source_message_id", "source_type"}


def test_ai_session_and_directives_do_not_own_conversation_close():
    assert "idle_close" not in SessionState.model_fields
    assert "CLOSE_PENDING" not in {status.value for status in ServiceStatus}

    with pytest.raises(ValidationError):
        ControlDirective(
            directive_id="directive-close",
            directive_type="conversation.close_requested",
            customer_notice="Closing this conversation.",
        )
