from app.graph.nodes import (
    build_graph_state_from_event,
    command_planner_node,
    human_handoff_node,
    intent_router_node,
    make_intent_router_node,
    prepare_route_state,
    rewrite_question_node,
)
from app.schemas.events import InboundEvent
from app.workflows.command_contracts import CommandType


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
    assert state["llm_rewrite_result"] is None
    assert state["llm_intent_result"] is None
    assert state["route_source"] == "deterministic"
    assert state["rewrite_source"] == "deterministic"


def test_rewrite_question_node_keeps_user_facts():
    result = rewrite_question_node({"raw_user_input": "mi usuario es andy123, deposito 50000 no llegó"})

    assert "andy123" in result["rewritten_question"]
    assert result["rewrite_result"]["mentioned_entities"]["amount"] == "50000"


def test_prepare_route_state_runs_rewrite_then_route():
    result = prepare_route_state({"raw_user_input": "Cómo puedo retirar"})

    assert result["rewritten_question"] == "Cómo puedo retirar"
    assert result["intent_result"]["intent"] == "withdrawal_howto"
    assert result["route"] == "faq"


def test_intent_router_node_routes_bot66tornado_samples():
    cases = [
        ("mi deposito no llegó", "deposit_missing", "sop"),
        ("Cómo puedo retirar", "withdrawal_howto", "faq"),
        ("Nunca me pagaron el retiro", "withdrawal_missing", "sop"),
        ("No puedo retirar", "withdrawal_blocked_or_rollover", "sop"),
        ("Tengo un caso anterior", "pending_reply_lookup", "sop"),
        ("no veo ningun menu", "clarification_needed", "final_reply"),
        ("Todo el tiempo lo mismo", "service_frustration", "human_handoff"),
        ("Problemas técnicos del juego", "unsupported_concrete_issue", "human_handoff"),
    ]

    for text, expected_intent, expected_route in cases:
        result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

        assert result["intent_result"]["intent"] == expected_intent
        assert result["route"] == expected_route


def test_intent_router_node_does_not_emit_sop_slots():
    text = "mi deposito no llegó, mi usuario es andy123"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["intent_result"]["intent"] == "deposit_missing"
    assert "account_or_phone" not in result["intent_result"]
    assert "deposit_screenshot" not in result["intent_result"]
    assert result.get("slot_memory") is None or result.get("slot_memory") == {}


def test_transaction_issue_must_not_route_to_faq():
    for text in ("我充值了没到账", "提款没到账", "无法提款", "流水不够"):
        result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

        assert result["route"] != "faq"


def test_canonical_howto_questions_route_to_faq():
    cases = [
        ("如何充值", "deposit_howto"),
        ("如何提款", "withdrawal_howto"),
        ("忘记密码", "forgot_password_howto"),
        ("如何上传截图", "screenshot_upload_howto"),
    ]

    for text, expected_intent in cases:
        result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

        assert result["route"] == "faq"
        assert result["intent_result"]["intent"] == expected_intent


def test_howto_issue_must_not_route_to_sop():
    text = "Cómo puedo retirar"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["route"] != "sop"


def test_active_collecting_workflow_supplement_routes_to_current_sop():
    result = intent_router_node(
        {
            "raw_user_input": "账号 abc123 金额1000",
            "rewritten_question": "账号 abc123 金额1000",
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
        }
    )

    assert result["intent_result"]["intent"] == "deposit_missing"
    assert result["route"] == "sop"
    assert result["intent_result"]["workflow_relation"] == "current_workflow_supplement"


def test_active_workflow_acknowledgement_routes_to_contextual_reply_without_sop_side_effect():
    result = intent_router_node(
        {
            "raw_user_input": "好的",
            "rewritten_question": "好的",
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "waiting_backend",
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "acknowledgement"
    assert result["intent_result"]["workflow_relation"] == "acknowledgement"
    assert result["intent_result"]["preserve_active_workflow"] is True
    assert result["node_reply_template"] == "acknowledgement"
    assert result["reply_plan"]["kind"] == "acknowledgement"


def test_active_workflow_name_offer_routes_to_contextual_followup():
    result = intent_router_node(
        {
            "raw_user_input": "May I provide my name?",
            "rewritten_question": "May I provide my name?",
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "collecting_slots",
            "slot_memory": {},
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "contextual_followup"
    assert result["intent_result"]["workflow_relation"] == "contextual_followup"
    assert result["intent_result"]["preserve_active_workflow"] is True
    assert result["node_reply_template"] == "contextual_followup"
    assert result["reply_plan"]["kind"] == "contextual_followup"


def test_active_collecting_workflow_allows_independent_faq_without_clearing_workflow():
    result = intent_router_node(
        {
            "raw_user_input": "怎么提款？",
            "rewritten_question": "怎么提款？",
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
            "slot_memory": {"account_or_phone": "abc123"},
        }
    )

    assert result["route"] == "faq"
    assert result["intent_result"]["intent"] == "withdrawal_howto"
    assert result["intent_result"]["workflow_relation"] == "independent_faq"
    assert result["active_workflow"] == "deposit_missing"
    assert result["slot_memory"] == {"account_or_phone": "abc123"}


def test_active_collecting_workflow_new_sop_request_asks_before_switching():
    result = intent_router_node(
        {
            "raw_user_input": "我还有一笔提款没到账",
            "rewritten_question": "我还有一笔提款没到账",
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["workflow_relation"] == "new_workflow_request"
    assert result["active_workflow"] == "deposit_missing"
    assert result["node_reply_template"] == "clarification"
    assert result["reply_plan"]["kind"] == "clarification"


def test_active_withdrawal_workflow_deposit_resolution_is_not_current_supplement():
    result = intent_router_node(
        {
            "raw_user_input": "Gracias.. ya llego el deposito",
            "rewritten_question": "Gracias.. ya llego el deposito",
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "waiting_backend",
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["workflow_relation"] == "new_workflow_request"


def test_without_active_workflow_greeting_routes_to_casual_chat():
    result = intent_router_node({"raw_user_input": "hello, how are you?", "rewritten_question": "hello, how are you?"})

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "casual_chat"
    assert result["node_reply_template"] == "default_final_reply"
    assert result["reply_plan"]["kind"] == "casual_chat"


def test_llm_intent_invalid_active_workflow_switch_falls_back_to_deterministic_faq():
    import asyncio

    class BadSwitchService:
        async def route(self, payload):
            return {
                "intent": "withdrawal_missing",
                "route": "sop",
                "confidence": 0.95,
                "sop_name": "withdrawal_missing",
                "requires_backend": True,
                "workflow_relation": "new_workflow_request",
                "preserve_active_workflow": True,
                "reason": "bad direct switch",
                "provider": "fake",
                "mode": "guarded_authoritative",
            }

    node = make_intent_router_node(BadSwitchService())
    result = asyncio.run(
        node(
            {
                "raw_user_input": "怎么提款？",
                "rewritten_question": "怎么提款？",
                "active_workflow": "deposit_missing",
                "workflow_stage": "collecting_slots",
            }
        )
    )

    assert result["route"] == "faq"
    assert result["intent_result"]["workflow_relation"] == "independent_faq"
    assert result["llm_router_result"]["status"] == "fallback"
    assert result["llm_router_result"]["fallback_reason"] == "validation_error"


def test_command_planner_node_prefers_final_response_text():
    result = command_planner_node(
        {
            "response_text": "fallback text",
            "final_response_text": "final composed text",
            "commands": [],
        }
    )

    assert result["commands"][0]["payload"]["text"] == "final composed text"


def test_human_handoff_node_emits_ack_text_before_handoff_request():
    result = human_handoff_node({"intent_result": {"intent": "service_frustration"}})

    assert result["active_workflow"] == "human_handoff"
    assert result["commands"] == [
        {
            "type": CommandType.LIVECHAT_SEND_TEXT,
            "payload": {"text": "我会为你转接真人客服继续协助。", "handoff_ack": True},
        },
        {
            "type": CommandType.HUMAN_HANDOFF_REQUESTED,
            "payload": {"reason": "service_frustration"},
        },
    ]


def test_command_planner_node_preserves_handoff_ack_when_updating_text():
    result = command_planner_node(
        {
            "final_response_text": "final handoff text",
            "commands": [
                {
                    "type": CommandType.LIVECHAT_SEND_TEXT,
                    "payload": {"text": "fallback", "handoff_ack": True},
                }
            ],
        }
    )

    assert result["commands"][0]["payload"] == {"text": "final handoff text", "handoff_ack": True}
