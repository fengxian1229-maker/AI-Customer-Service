from app.graph.nodes import (
    build_graph_state_from_event,
    intent_router_node,
    rewrite_question_node,
    signal_judgement_node,
)
from app.schemas.events import InboundEvent


def make_event(text: str = "hola", event_type: str = "MESSAGE_CREATED", payload: dict | None = None) -> InboundEvent:
    payload_json = payload or {
        "event": {
            "type": "message",
            "text": text,
        }
    }
    return InboundEvent(
        source="polling_fallback",
        raw_action="polling.event",
        chat_id="chat-1",
        thread_id="thread-1",
        event_id="event-1",
        event_type="message" if event_type == "MESSAGE_CREATED" else "file",
        standard_event_type=event_type,
        author_id="user-1",
        sender_role="external",
        occurred_at="2026-06-24 00:00:00.000000",
        dedup_key="key",
        payload_json=payload_json,
        ignored=False,
    )


def test_build_graph_state_from_event_extracts_text_context_and_attachments():
    event = make_event(
        event_type="FILE_RECEIVED",
        payload={
            "event": {
                "type": "file",
                "url": "https://cdn.example/screenshot.png",
                "name": "screenshot.png",
            }
        },
    )

    state = build_graph_state_from_event(
        event,
        {"conversation_id": "livechat:chat-1", "active_workflow": "deposit_missing", "slot_memory": {"x": 1}},
    )

    assert state["conversation_id"] == "livechat:chat-1"
    assert state["active_workflow"] == "deposit_missing"
    assert state["event_type"] == "FILE_RECEIVED"
    assert state["attachments"] == [{"url": "https://cdn.example/screenshot.png", "name": "screenshot.png"}]


def test_rewrite_question_node_keeps_user_facts():
    result = rewrite_question_node({"raw_user_input": "mi usuario es andy123, deposito 50000 no llegó"})

    assert "andy123" in result["rewritten_question"]
    assert result["rewrite_result"]["mentioned_entities"]["amount"] == "50000"


def test_signal_judgement_node_recognizes_identity_values():
    result = signal_judgement_node({"rewritten_question": "mi correo es test@example.com"})
    assert result["signal_result"]["has_identity"] is True
    assert result["signal_result"]["identity_type"] == "email"

    result = signal_judgement_node({"rewritten_question": "mi telefono es +57 300 123 4567"})
    assert result["signal_result"]["identity_type"] == "phone"

    result = signal_judgement_node({"rewritten_question": "mi usuario es andy123"})
    assert result["signal_result"]["identity_type"] == "username"


def test_signal_judgement_node_recognizes_explicit_human_request():
    result = signal_judgement_node({"rewritten_question": "quiero hablar con un agente humano"})

    assert result["signal_result"]["has_explicit_human_request"] is True


def test_intent_router_node_routes_withdrawal_missing():
    state = signal_judgement_node({"rewritten_question": "mi retiro no llegó"})
    result = intent_router_node({**state, "rewritten_question": "mi retiro no llegó"})

    assert result["intent_result"]["intent"] == "withdrawal_missing"
    assert result["route"] == "sop"


def test_intent_router_node_routes_withdrawal_blocked_or_rollover():
    state = signal_judgement_node({"rewritten_question": "no puedo retirar por流水"})
    result = intent_router_node({**state, "rewritten_question": "no puedo retirar por流水"})

    assert result["intent_result"]["intent"] == "withdrawal_blocked_or_rollover"
    assert result["intent_result"]["requires_backend"] is True


def test_intent_router_node_routes_human_handoff():
    state = signal_judgement_node({"rewritten_question": "quiero un agente"})
    result = intent_router_node({**state, "rewritten_question": "quiero un agente"})

    assert result["intent_result"]["intent"] == "human_handoff"
    assert result["route"] == "human_handoff"
