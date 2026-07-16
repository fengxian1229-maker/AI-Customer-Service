import asyncio

from app.graph.nodes import (
    build_graph_state_from_event,
    command_planner_node,
    human_handoff_node,
    intent_router_node,
    make_intent_router_node,
    prepare_route_state,
    rag_node,
    rewrite_question_node,
    sop_node,
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
    assert state["event_id"] == "event-1"
    assert state["active_workflow"] == "deposit_missing"
    assert state["event_type"] == "FILE_RECEIVED"
    assert state["occurred_at"] == "2026-06-24 00:00:00.000000"
    assert state["attachments"] == [{"url": "https://cdn.example/screenshot.png", "name": "screenshot.png"}]
    assert state["llm_rewrite_result"] is None
    assert state["llm_intent_result"] is None
    assert state["route_source"] == "deterministic"
    assert state["rewrite_source"] == "deterministic"


def test_build_graph_state_copies_internal_money_case_candidates():
    candidates = [{"id": 8, "intent": "withdrawal_missing", "root_message_id": 200}]

    state = build_graph_state_from_event(
        make_event("still not received"),
        {
            "conversation_id": "livechat:chat-1:thread-new",
            "slot_memory": {},
            "telegram_money_case_candidates": candidates,
        },
    )

    assert state["telegram_money_case_candidates"] == candidates


def test_intent_router_matches_cross_thread_money_case_before_generic_route():
    state = prepare_route_state(
        {
            "raw_user_input": "TX200 is still not received",
            "slot_memory": {},
            "telegram_money_case_candidates": [
                {
                    "id": 8,
                    "intent": "withdrawal_missing",
                    "status": "under_review",
                    "telegram_chat_id": "-1001",
                    "root_message_id": 200,
                    "slot_memory": {"order_id": "TX200"},
                }
            ],
        }
    )

    assert state["route"] == "sop"
    assert state["active_workflow"] == "withdrawal_missing"
    assert state["workflow_stage"] == "waiting_backend"
    assert state["matched_telegram_money_case"]["id"] == 8


def test_intent_router_clarifies_when_multiple_money_cases_are_ambiguous():
    state = prepare_route_state(
        {
            "raw_user_input": "still not received",
            "slot_memory": {},
            "reply_language": "en",
            "telegram_money_case_candidates": [
                {"id": 8, "intent": "deposit_missing", "status": "under_review", "root_message_id": 200},
                {"id": 9, "intent": "withdrawal_missing", "status": "under_review", "root_message_id": 201},
            ],
        }
    )

    assert state["route"] == "final_reply"
    assert "transaction" in state["response_text"].lower()
    assert state["commands"] == []


def test_build_graph_state_preserves_image_attachment_metadata_and_candidates():
    event = make_event(
        event_type="FILE_RECEIVED",
        payload={
            "event": {
                "type": "file",
                "url": "https://cdn.example/deposit-receipt.png",
                "name": "deposit-receipt.png",
                "mime_type": "image/png",
                "image_analysis": {
                    "candidate_intents": ["deposit_missing_candidate"],
                    "receipt_kind": "deposit",
                    "is_receipt_like": True,
                    "confidence": 0.91,
                },
            }
        },
    )

    state = build_graph_state_from_event(event, {"conversation_id": "livechat:chat-1", "slot_memory": {}})

    assert state["attachments"][0]["mime_type"] == "image/png"
    assert state["attachments"][0]["content_type"] == "image/png"
    assert state["image_analysis"]["candidate_intents"] == ["deposit_missing_candidate"]
    assert state["image_candidate_only"] is True


def test_sop_node_resolves_active_workflow_even_when_stage_is_collecting_slots():
    result = sop_node(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
            "intent_result": {
                "intent": "deposit_missing",
                "route": "sop",
                "workflow_relation": "current_workflow_resolution",
                "preserve_active_workflow": False,
            },
            "slot_memory": {
                "last_telegram_staff_reply_type": "resolution",
                "telegram_case_id": "tg:53",
                "telegram_message_id": 53,
            },
            "raw_user_input": "thanks",
            "rewritten_question": "thanks",
            "reply_language": "en",
            "attachments": [],
            "recent_messages": [
                {
                    "sender_role": "assistant",
                    "text_content": "I have verified that your deposit has been successfully credited to your account.",
                }
            ],
        }
    )

    assert result["commands"] == []
    assert result["sop_action"] == "customer_confirmed_resolved"
    assert result["workflow_stage"] == "completed"
    assert result["active_workflow"] is None
    assert result["slot_memory"]["customer_confirmed_resolved"] is True
    assert result["response_text"].startswith("Thanks for letting us know")


def test_llm_router_cannot_override_cross_thread_money_case_match():
    class RouterService:
        async def route(self, payload):
            raise AssertionError("Cross-thread money-case matching must run before the LLM router")

    result = asyncio.run(
        make_intent_router_node(RouterService())(
            {
                "raw_user_input": "TX200 is still not received",
                "rewritten_question": "TX200 is still not received",
                "slot_memory": {},
                "telegram_money_case_candidates": [
                    {
                        "id": 8,
                        "intent": "withdrawal_missing",
                        "status": "under_review",
                        "telegram_chat_id": "-1001",
                        "root_message_id": 200,
                        "slot_memory": {"order_id": "TX200"},
                    }
                ],
            }
        )
    )

    assert result["route"] == "sop"
    assert result["route_locked"] is True
    assert result["matched_telegram_money_case"]["id"] == 8
    assert result["llm_router_result"]["fallback_reason"] == "cross_thread_money_case_guard"


def test_cross_thread_customer_confirmation_routes_to_resolution_without_reminder():
    routed = intent_router_node(
        {
            "raw_user_input": "TX200 has arrived now",
            "rewritten_question": "TX200 has arrived now",
            "slot_memory": {},
            "telegram_money_case_candidates": [
                {
                    "id": 8,
                    "intent": "withdrawal_missing",
                    "status": "completed_by_staff",
                    "root_message_id": 200,
                    "slot_memory": {"order_id": "TX200"},
                }
            ],
        }
    )

    assert routed["route"] == "sop"
    assert routed["intent_result"]["workflow_relation"] == "current_workflow_resolution"
    assert routed["slot_memory"]["telegram_internal_case_id"] == 8

    resolved = sop_node({**routed, "reply_language": "en", "attachments": []})
    assert resolved["commands"] == []
    assert resolved["telegram_case_update"] == {
        "telegram_case_id": 8,
        "status": "completed_confirmed_by_customer",
    }


def test_sop_node_does_not_resolve_plain_thanks_without_resolution_context():
    result = sop_node(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
            "intent_result": {
                "intent": "deposit_missing",
                "route": "sop",
                "workflow_relation": "current_workflow_resolution",
            },
            "slot_memory": {},
            "raw_user_input": "thanks",
            "rewritten_question": "thanks",
            "reply_language": "en",
            "attachments": [],
            "recent_messages": [
                {
                    "sender_role": "assistant",
                    "text_content": "Please send your registered phone number and deposit screenshot.",
                }
            ],
        }
    )

    assert result["active_workflow"] == "deposit_missing"
    assert result["workflow_stage"] == "collecting_slots"
    assert result["sop_action"] != "customer_confirmed_resolved"


def test_image_only_deposit_candidate_asks_confirmation_without_entering_sop():
    result = intent_router_node(
        {
            "event_type": "FILE_RECEIVED",
            "raw_user_input": "",
            "rewritten_question": "",
            "attachments": [{"url": "https://cdn.example/deposit.png", "mime_type": "image/png"}],
            "image_analysis": {
                "candidate_intents": ["deposit_missing_candidate"],
                "receipt_kind": "deposit",
                "is_receipt_like": True,
                "confidence": 0.9,
            },
            "slot_memory": {},
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "image_deposit_candidate"
    assert result["active_workflow"] is None
    assert result["image_candidate_only"] is True
    assert "存款" in result["response_text"]
    assert result["commands"][0]["type"] == CommandType.LIVECHAT_SEND_TEXT


def test_image_only_unknown_candidate_asks_user_to_describe_issue():
    result = intent_router_node(
        {
            "event_type": "FILE_RECEIVED",
            "raw_user_input": "",
            "rewritten_question": "",
            "attachments": [{"url": "https://cdn.example/landscape.png", "mime_type": "image/png"}],
            "image_analysis": {
                "candidate_intents": ["unknown_image"],
                "receipt_kind": "unknown",
                "is_receipt_like": False,
                "confidence": 0.2,
            },
            "slot_memory": {},
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "image_unknown"
    assert result["active_workflow"] is None
    assert "补充" in result["response_text"]


def test_confirmed_deposit_image_candidate_enters_sop():
    result = intent_router_node(
        {
            "event_type": "MESSAGE_CREATED",
            "raw_user_input": "是，帮我查存款",
            "rewritten_question": "是，帮我查存款",
            "slot_memory": {
                "pending_image_candidates": [
                    {
                        "attachment_url": "https://cdn.example/deposit.png",
                        "candidate_intents": ["deposit_missing_candidate"],
                        "receipt_kind": "deposit",
                        "is_receipt_like": True,
                        "confidence": 0.9,
                    }
                ]
            },
        }
    )

    assert result["route"] == "sop"
    assert result["intent_result"]["intent"] == "deposit_missing"
    assert result["slot_memory"]["verified_receipt_attachments"][0]["url"] == "https://cdn.example/deposit.png"


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
        ("Todo el tiempo lo mismo", "service_frustration", "emotion_care"),
        ("Problemas técnicos del juego", "game_technical_issue", "human_handoff"),
    ]

    for text, expected_intent, expected_route in cases:
        result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

        assert result["intent_result"]["intent"] == expected_intent
        assert result["route"] == expected_route


def test_auto_handoff_p0_risk_scenarios():
    cases = [
        ("截图上传不了，一直失败", "screenshot_upload_failed"),
        ("我的提款银行卡身份资料异常", "wallet_identity_risk"),
        ("验证码收不到，SIM 验证不了", "account_verification_issue"),
        ("优惠码 bonus 注册退款问题", "promo_refund_unsupported"),
        ("游戏技术问题打不开", "game_technical_issue"),
        ("你们是不是诈骗，资金安全有问题", "abuse_or_fraud_risk"),
        ("我按照教程做了还是不行", "tutorial_failed_aftercare"),
    ]

    for text, expected_intent in cases:
        result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

        assert result["route"] == "human_handoff"
        assert result["intent_result"]["intent"] == expected_intent


def test_menu_stuck_repeated_handoffs_on_second_attempt():
    first = intent_router_node(
        {
            "rewritten_question": "no veo ningun menu",
            "raw_user_input": "no veo ningun menu",
            "slot_memory": {},
        }
    )
    second = intent_router_node(
        {
            "rewritten_question": "no veo ningun menu",
            "raw_user_input": "no veo ningun menu",
            "slot_memory": first["slot_memory"],
        }
    )

    assert first["route"] == "final_reply"
    assert second["route"] == "human_handoff"
    assert second["intent_result"]["intent"] == "menu_stuck_repeated"


def test_active_workflow_conflict_with_existing_data_handoffs():
    result = intent_router_node(
        {
            "raw_user_input": "我还有一笔提款没到账",
            "rewritten_question": "我还有一笔提款没到账",
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
            "slot_memory": {"account_or_phone": "abc123", "amount": "1000"},
        }
    )

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "active_workflow_conflict_with_data"


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


def test_emotional_deposit_missing_routes_to_sop_with_risk_signal():
    text = "你们真垃圾，我的存款没到账"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["route"] == "sop"
    assert result["intent_result"]["route"] == "sop"
    assert result["intent_result"]["intent"] == "deposit_missing"
    assert result["intent_result"]["risk_level"] in {"high", "elevated"}


def test_emotional_withdrawal_missing_routes_to_sop():
    text = "scam, my withdrawal did not arrive"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "abuse_or_fraud_risk"


def test_emotional_withdrawal_blocked_routes_to_sop():
    text = "你们是骗子，我无法提款，说我流水不够"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "abuse_or_fraud_risk"


def test_pure_emotional_message_routes_to_emotion_care():
    text = "你们真垃圾"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["route"] == "emotion_care"
    assert result["intent_result"]["intent"] in {"abusive_or_emotional", "service_frustration"}


def test_emotional_explicit_human_request_routes_to_handoff():
    text = "你们垃圾，我要真人客服"
    result = intent_router_node({"rewritten_question": text, "raw_user_input": text})

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "explicit_human_request"


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


def test_forgot_password_faq_marks_followup_context():
    result = rag_node(
        {
            "intent_result": {"intent": "forgot_password_howto", "faq_query": "忘记密码"},
            "raw_user_input": "忘记密码",
        }
    )

    assert result["active_workflow"] == "forgot_password_howto"
    assert result["workflow_stage"] == "awaiting_password_reset_result"


def test_forgot_password_followup_failure_routes_to_handoff():
    result = intent_router_node(
        {
            "active_workflow": "forgot_password_howto",
            "workflow_stage": "awaiting_password_reset_result",
            "raw_user_input": "还是不行",
            "rewritten_question": "还是不行",
        }
    )

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "forgot_password_followup_failed"


def test_forgot_password_followup_screenshot_routes_to_handoff():
    result = intent_router_node(
        {
            "active_workflow": "forgot_password_howto",
            "workflow_stage": "awaiting_password_reset_result",
            "event_type": "FILE_RECEIVED",
            "attachments": [{"url": "https://cdn.example/error.png", "mime_type": "image/png"}],
            "raw_user_input": "",
            "rewritten_question": "",
        }
    )

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "forgot_password_followup_failed"


def test_forgot_password_followup_overrides_llm_authoritative_image_route():
    result = intent_router_node(
        {
            "active_workflow": "forgot_password_howto",
            "workflow_stage": "awaiting_password_reset_result",
            "event_type": "FILE_RECEIVED",
            "attachments": [{"url": "https://cdn.example/error.png", "mime_type": "image/png"}],
            "raw_user_input": "",
            "rewritten_question": "",
            "route": "final_reply",
            "route_source": "llm_guarded_authoritative",
            "intent_result": {"intent": "image_unknown", "route": "final_reply"},
        }
    )

    assert result["route"] == "human_handoff"
    assert result["intent_result"]["intent"] == "forgot_password_followup_failed"


def test_attachment_without_forgot_password_context_does_not_route_to_handoff():
    result = intent_router_node(
        {
            "event_type": "FILE_RECEIVED",
            "attachments": [{"url": "https://cdn.example/unknown.png", "mime_type": "image/png"}],
            "raw_user_input": "",
            "rewritten_question": "",
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "clarification_needed"


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


def test_active_workflow_rollover_explanation_uses_contextual_final_reply():
    result = intent_router_node(
        {
            "raw_user_input": "流水是什么",
            "rewritten_question": "流水是什么",
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "waiting_backend",
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] != "rollover_explanation"
    assert result["node_reply_template"] in {"contextual_followup", "clarification"}
    assert result["reply_plan"]["kind"] in {"contextual_followup", "clarification"}


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


def test_sop_node_backend_replied_phone_supplement_appends_to_case():
    result = sop_node(
        {
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "backend_replied",
            "status": "AI_ACTIVE",
            "route": "sop",
            "intent_result": {
                "intent": "withdrawal_missing",
                "route": "sop",
                "workflow_relation": "current_sop_supplement",
                "preserve_active_workflow": True,
            },
            "slot_memory": {
                "account_or_phone": "indica",
                "identity_kind": "username",
                "telegram_case_id": "tg:59117",
                "telegram_message_id": 59117,
                "telegram_target_chat_id": "-1003181576378",
                "telegram_message_thread_id": 36735,
            },
            "raw_user_input": "3135426895",
            "rewritten_question": "El usuario proporciona su número de teléfono registrado: 3135426895.",
            "reply_language": "es",
            "attachments": [],
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"phone": "3135426895"},
                "slot_confidence": {"phone": 1.0},
                "reason": "phone supplied",
            },
        }
    )

    assert result["slot_memory"]["phone"] == "3135426895"
    assert result["slot_memory"]["account_or_phone"] == "3135426895"
    assert result["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert result["commands"][0]["payload"]["telegram_case_id"] == "tg:59117"
    supplement_updates = result["commands"][0]["payload"]["supplement"]["slot_updates"]
    assert supplement_updates["phone"] == "3135426895"
    assert supplement_updates["identity_source"] == "user_text"


def test_intent_router_handoffs_when_customer_disputes_recent_telegram_resolution():
    from app.graph.nodes import human_handoff_node, intent_router_node

    routed = intent_router_node(
        {
            "raw_user_input": "Ya mire y no me a llegafo",
            "rewritten_question": "Ya mire y no me a llegafo",
            "occurred_at": "2026-07-13 08:07:53",
            "slot_memory": {
                "telegram_case_resolved_at": "2026-07-13 08:07:12",
                "telegram_case_resolution_workflow": "withdrawal_missing",
                "telegram_case_resolution_type": "resolution",
                "customer_confirmed_resolved": False,
            },
        }
    )

    assert routed["route"] == "human_handoff"
    assert routed["intent_result"]["intent"] == "authoritative_result_disputed"
    assert routed["intent_result"]["workflow_relation"] == "human_escalation"

    handed_off = human_handoff_node(routed)
    assert handed_off["status"] == "HANDOFF_REQUESTED"
    assert [command["type"] for command in handed_off["commands"]] == [
        CommandType.LIVECHAT_SEND_TEXT,
        CommandType.HUMAN_HANDOFF_REQUESTED,
    ]
    assert handed_off["commands"][1]["payload"]["reason"] == "authoritative_result_disputed"


def test_graph_state_event_time_activates_recent_resolution_dispute_guard():
    event = make_event(text="Ya mire y no me a llegafo")
    state = build_graph_state_from_event(
        event,
        {
            "conversation_id": "livechat:chat-1",
            "slot_memory": {
                "telegram_case_resolved_at": "2026-06-23 23:59:30.000000",
                "telegram_case_resolution_workflow": "withdrawal_missing",
                "customer_confirmed_resolved": False,
            },
        },
    )

    routed = intent_router_node(state)

    assert routed["route"] == "human_handoff"
    assert routed["intent_result"]["intent"] == "authoritative_result_disputed"


def test_intent_router_does_not_force_handoff_for_recent_resolution_acknowledgement():
    from app.graph.nodes import intent_router_node

    routed = intent_router_node(
        {
            "raw_user_input": "Gracias",
            "rewritten_question": "Gracias",
            "occurred_at": "2026-07-13 08:07:53",
            "slot_memory": {
                "telegram_case_resolved_at": "2026-07-13 08:07:12",
                "telegram_case_resolution_workflow": "withdrawal_missing",
                "customer_confirmed_resolved": False,
            },
        }
    )

    assert (routed.get("intent_result") or {}).get("intent") != "authoritative_result_disputed"


def test_llm_intent_router_cannot_override_recent_resolution_dispute_guard():
    from app.graph.nodes import make_intent_router_node

    class RouterService:
        async def route(self, payload):
            raise AssertionError("Resolution dispute guard must run before the LLM router")

    routed = asyncio.run(
        make_intent_router_node(RouterService())(
            {
                "raw_user_input": "I checked and still have not received it",
                "occurred_at": "2026-07-13 08:10:00",
                "slot_memory": {
                    "telegram_case_resolved_at": "2026-07-13 08:07:12",
                    "telegram_case_resolution_workflow": "withdrawal_missing",
                    "customer_confirmed_resolved": False,
                },
            }
        )
    )

    assert routed["route"] == "human_handoff"
    assert routed["intent_result"]["intent"] == "authoritative_result_disputed"
    assert routed["llm_router_result"]["fallback_reason"] == "authoritative_result_disputed_guard"


def _backend_dispute_memory(count: int = 0) -> dict:
    return {
        "backend_conclusion": {
            "intent": "withdrawal_blocked_or_rollover",
            "reply_intent": "backend_turnover_remaining",
            "reply_facts": {"remaining_turnover": "18.88"},
            "fingerprint": "fp-18.88",
            "recorded_at": "2026-07-15T03:19:41Z",
        },
        "backend_dispute_count": count,
    }


def test_intent_router_rechecks_first_backend_dispute_and_waits_on_second_while_pending():
    first = intent_router_node(
        {
            "event_id": "event-1",
            "raw_user_input": "Ya intenté retirar cuatro veces y siempre lo devuelven",
            "rewritten_question": "Ya intenté retirar cuatro veces y siempre lo devuelven",
            "slot_memory": _backend_dispute_memory(),
        }
    )
    second = intent_router_node(
        {
            **first,
            "event_id": "event-2",
            "raw_user_input": "Siempre me dicen que juegue y después aparece retiro fallido",
            "rewritten_question": "Siempre me dicen que juegue y después aparece retiro fallido",
        }
    )

    assert first["route"] == "sop"
    assert first["slot_memory"]["backend_dispute_count"] == 1
    assert first["slot_memory"]["backend_recheck_pending"] is True
    assert second["route"] == "sop"
    assert second["slot_memory"]["backend_dispute_count"] == 1
    assert second["slot_memory"]["backend_recheck_queued_dispute"] is True
    assert second["intent_result"]["intent"] == "withdrawal_blocked_or_rollover"


def test_llm_intent_router_cannot_override_pending_backend_recheck_wait():
    class RouterService:
        async def route(self, payload):
            raise AssertionError("Repeated backend dispute guard must run before the LLM router")

    routed = asyncio.run(
        make_intent_router_node(RouterService())(
            {
                "event_id": "event-2",
                "raw_user_input": "Siempre me dicen que juegue y después aparece retiro fallido",
                "slot_memory": {
                    **_backend_dispute_memory(count=1),
                    "backend_recheck_pending": True,
                    "backend_recheck_origin_fingerprint": "fp-18.88",
                },
            }
        )
    )

    assert routed["route"] == "sop"
    assert routed["intent_result"]["intent"] == "withdrawal_blocked_or_rollover"
    assert routed["slot_memory"]["backend_recheck_queued_dispute"] is True
    assert routed["llm_router_result"]["fallback_reason"] == "backend_conclusion_disputed_guard"


def test_backend_dispute_counter_clears_on_acceptance():
    routed = intent_router_node(
        {
            "event_id": "event-2",
            "raw_user_input": "Gracias, listo",
            "rewritten_question": "Gracias, listo",
            "slot_memory": _backend_dispute_memory(count=1),
        }
    )

    assert "backend_dispute_count" not in routed["slot_memory"]


def test_backend_dispute_counter_clears_when_customer_changes_business_topic():
    routed = intent_router_node(
        {
            "event_id": "event-2",
            "raw_user_input": "Cómo puedo hacer un depósito",
            "rewritten_question": "Cómo puedo hacer un depósito",
            "slot_memory": _backend_dispute_memory(count=1),
        }
    )

    assert routed["intent_result"]["intent"] != "withdrawal_blocked_or_rollover"
    assert "backend_dispute_count" not in routed["slot_memory"]


def test_conversation_memory_lookup_uses_recent_customer_message():
    result = intent_router_node(
        {
            "raw_user_input": "我刚刚说什么了？",
            "rewritten_question": "我刚刚说什么了？",
            "recent_messages": [
                {"sender_role": "customer", "text_content": "我忘记我的密码了"},
                {"sender_role": "assistant", "text_content": "请按照页面提示重设密码。"},
                {"sender_role": "customer", "text_content": "我刚刚说什么了？"},
            ],
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "conversation_memory_lookup"
    assert result["node_reply_template"] == "contextual_followup"
    assert result["response_text"] == "你上一句说的是：我忘记我的密码了"


def test_withdrawal_reason_recall_uses_recent_backend_reply_not_pending_case_lookup():
    result = intent_router_node(
        {
            "raw_user_input": "刚刚是说我因为什么原因导致无法提款来着？",
            "rewritten_question": "刚刚是说我因为什么原因导致无法提款来着？",
            "active_workflow": "pending_reply_lookup",
            "workflow_stage": "lookup_pending_reply",
            "slot_memory": {
                "account_or_phone": "3239413629",
                "backend_query_status": "success",
                "pending_reply_identity": "3239413629",
            },
            "recent_messages": [
                {"sender_role": "assistant", "text_content": "您好，一般无法提款通常与流水要求或风控限制有关。"},
                {"sender_role": "assistant", "text_content": "您好，经查询您的账号 3239413629，目前尚有未完成的流水要求。系统显示您当前的剩余流水为 1375.09。请您在满足该流水要求后再次尝试操作提款。"},
                {"sender_role": "customer", "text_content": "好的，谢谢您"},
                {"sender_role": "assistant", "text_content": "不用客气。如果后续有存款、提款、流水相关的问题，或需要真人客服协助，请随时告知我。"},
            ],
        }
    )

    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "conversation_memory_lookup"
    assert result["intent_result"]["preserve_active_workflow"] is True
    assert result["node_reply_template"] == "contextual_followup"
    assert "剩余流水为 1375.09" in result["response_text"]
    assert result["commands"] == []


def test_explicit_previous_case_still_routes_to_pending_reply_lookup():
    result = intent_router_node({"raw_user_input": "上一笔案件", "rewritten_question": "上一笔案件"})

    assert result["route"] == "sop"
    assert result["intent_result"]["intent"] == "pending_reply_lookup"


def test_llm_router_memory_guard_prevents_pending_case_misroute():
    import asyncio

    class PendingCaseService:
        def __init__(self):
            self.calls = []

        async def route(self, payload):
            self.calls.append(payload)
            return {
                "intent": "pending_reply_lookup",
                "route": "sop",
                "confidence": 0.95,
                "sop_name": "pending_reply_lookup",
                "requires_backend": True,
                "reason": "bad previous case classification",
                "provider": "fake",
                "mode": "guarded_authoritative",
            }

    service = PendingCaseService()
    node = make_intent_router_node(service)
    result = asyncio.run(
        node(
            {
                "raw_user_input": "刚刚是说我因为什么原因导致无法提款来着？",
                "rewritten_question": "刚刚是说我因为什么原因导致无法提款来着？",
                "active_workflow": "pending_reply_lookup",
                "workflow_stage": "lookup_pending_reply",
                "recent_messages": [
                    {"sender_role": "assistant", "text_content": "您好，经查询您的账号 3239413629，目前尚有未完成的流水要求。系统显示您当前的剩余流水为 1375.09。"},
                ],
            }
        )
    )

    assert service.calls == []
    assert result["route"] == "final_reply"
    assert result["intent_result"]["intent"] == "conversation_memory_lookup"
    assert "1375.09" in result["response_text"]
    assert result["llm_router_result"]["fallback_reason"] == "conversation_memory_guard"


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


def test_forgot_password_handoff_ack_mentions_visible_error_screenshot():
    result = human_handoff_node(
        {
            "intent_result": {"intent": "forgot_password_followup_failed"},
            "attachments": [{"url": "https://cdn.example/error.png"}],
        }
    )

    assert "当前聊天中的错误截图" in result["response_text"]
    assert result["commands"][1] == {
        "type": CommandType.HUMAN_HANDOFF_REQUESTED,
        "payload": {"reason": "forgot_password_followup_failed"},
    }


def test_forgot_password_handoff_without_image_mentions_only_chat_history():
    result = human_handoff_node(
        {
            "intent_result": {"intent": "forgot_password_followup_failed"},
            "attachments": [],
        }
    )

    assert "当前聊天记录" in result["response_text"]
    assert "错误截图" not in result["response_text"]


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
