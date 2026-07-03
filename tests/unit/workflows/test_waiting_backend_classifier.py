from app.workflows.command_contracts import CommandType
from app.workflows.waiting_backend_classifier import handle_waiting_backend


def test_waiting_backend_attachment_without_telegram_case_does_not_append():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {"forwarded_attachment_urls": []},
            "attachments": [{"url": "https://cdn.example/supplement.png"}],
        }
    )

    assert state["commands"] == []
    assert state["sop_action"] == "waiting_followup"
    assert state["response_text"] == "案件仍在确认中，有更新会在这里通知你。"
    assert "forwarded_attachment_urls" not in state["slot_memory"] or state["slot_memory"]["forwarded_attachment_urls"] == []


def test_waiting_backend_attachment_with_telegram_case_generates_append_command():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {
                "telegram_case_id": "tg:123",
                "telegram_message_id": 123,
                "forwarded_attachment_urls": [],
            },
            "attachments": [
                {
                    "url": "https://cdn.example/supplement.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "deposit",
                }
            ],
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    payload = state["commands"][0]["payload"]
    assert payload["telegram_case_id"] == "tg:123"
    assert payload["telegram_message_id"] == 123
    assert payload["supplement"]["attachment_urls"] == ["https://cdn.example/supplement.png"]


def test_waiting_backend_attachment_and_human_with_telegram_case_appends_first():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {
                "telegram_case_id": "tg:123",
                "telegram_message_id": 123,
                "forwarded_attachment_urls": [],
            },
            "attachments": [
                {
                    "url": "https://cdn.example/new.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "deposit",
                }
            ],
            "raw_user_input": "quiero hablar con un agente humano",
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert state["commands"][0]["payload"]["telegram_message_id"] == 123
    assert state["commands"][0]["payload"]["supplement"]["attachment_urls"] == ["https://cdn.example/new.png"]
    assert all(command["type"] != CommandType.HUMAN_HANDOFF_REQUESTED for command in state["commands"])


def test_waiting_backend_attachment_and_human_without_telegram_case_handoffs():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {"forwarded_attachment_urls": []},
            "attachments": [{"url": "https://cdn.example/new.png"}],
            "raw_user_input": "quiero hablar con un agente humano",
        }
    )

    assert state["status"] == "HANDOFF_REQUESTED"
    assert state["commands"][0]["type"] == CommandType.HUMAN_HANDOFF_REQUESTED
    assert all(command["type"] != CommandType.TELEGRAM_APPEND_TO_CASE for command in state["commands"])


def test_waiting_backend_human_request_generates_handoff_command():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {},
            "attachments": [],
            "raw_user_input": "quiero hablar con un agente humano",
        }
    )

    assert state["status"] == "HANDOFF_REQUESTED"
    assert state["commands"][0]["type"] == CommandType.HUMAN_HANDOFF_REQUESTED


def test_waiting_backend_followup_only_reassures():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["response_text"] == "案件仍在确认中，有更新会在这里通知你。"
    assert state["commands"] == []


def test_waiting_backend_repeated_dispute_handoffs_on_second_attempt():
    first = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {},
            "attachments": [],
            "raw_user_input": "还要等多久，为什么还没处理",
        }
    )
    second = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": first["slot_memory"],
            "attachments": [],
            "raw_user_input": "还要等多久，为什么还没处理",
        }
    )

    assert first["commands"] == []
    assert second["status"] == "HANDOFF_REQUESTED"
    assert second["commands"][0]["type"] == CommandType.HUMAN_HANDOFF_REQUESTED
    assert second["commands"][0]["payload"]["reason"] == "waiting_backend_repeat_dispute"


def test_waiting_backend_acknowledgement_does_not_append_or_expose_case_id():
    state = handle_waiting_backend(
        {
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {"telegram_case_id": "tg:21", "telegram_message_id": 21},
            "attachments": [],
            "raw_user_input": "好的",
            "intent_result": {
                "intent": "acknowledgement",
                "route": "contextual_reply",
                "workflow_relation": "acknowledgement",
                "preserve_active_workflow": True,
            },
        }
    )

    assert state["commands"] == []
    assert state["sop_action"] == "acknowledgement"
    assert "tg:" not in state["response_text"]
    assert state["workflow_stage"] == "waiting_backend"


def test_waiting_backend_contextual_followup_answers_name_offer_without_append():
    state = handle_waiting_backend(
        {
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "collecting_slots",
            "slot_memory": {},
            "attachments": [],
            "raw_user_input": "May I provide my name?",
            "reply_language": "en",
            "intent_result": {
                "intent": "contextual_followup",
                "route": "contextual_reply",
                "workflow_relation": "contextual_followup",
                "preserve_active_workflow": True,
            },
        }
    )

    assert state["commands"] == []
    assert state["sop_action"] == "contextual_followup"
    assert "name" in state["response_text"].lower()
    assert "phone" in state["response_text"].lower()
    assert "screenshot" in state["response_text"].lower()


def test_waiting_backend_text_supplement_without_telegram_case_does_not_append():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {},
            "attachments": [],
            "raw_user_input": "补一下截图，交易号 TX123456",
        }
    )

    assert state["commands"] == []
    assert state["sop_action"] == "waiting_followup"


def test_waiting_backend_text_supplement_with_telegram_case_generates_append_command():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {"telegram_case_id": "tg:123", "telegram_message_id": 123},
            "attachments": [],
            "raw_user_input": "补一下交易号 TX123456",
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert state["commands"][0]["payload"]["telegram_case_id"] == "tg:123"
    assert state["commands"][0]["payload"]["telegram_message_id"] == 123
    assert state["commands"][0]["payload"]["supplement"]["text"] == "补一下交易号 TX123456"


def test_waiting_customer_supplement_phone_correction_generates_append_command():
    state = handle_waiting_backend(
        {
            "active_workflow": "withdrawal_missing",
            "workflow_stage": "waiting_customer_supplement",
            "slot_memory": {
                "telegram_case_id": "tg:21",
                "telegram_message_id": 21,
                "last_telegram_staff_reply_type": "ask_customer",
            },
            "attachments": [],
            "raw_user_input": "那电话应该是123456",
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert state["commands"][0]["payload"]["telegram_case_id"] == "tg:21"
    assert state["commands"][0]["payload"]["telegram_message_id"] == 21
    assert state["commands"][0]["payload"]["supplement"]["text"] == "那电话应该是123456"


def test_waiting_backend_text_supplement_and_human_with_telegram_case_appends_first():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {"telegram_case_id": "tg:123", "telegram_message_id": 123},
            "attachments": [],
            "raw_user_input": "交易号 TX123456, quiero humano",
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert "TX123456" in state["commands"][0]["payload"]["supplement"]["text"]
    assert all(command["type"] != CommandType.HUMAN_HANDOFF_REQUESTED for command in state["commands"])


def test_waiting_backend_llm_screenshot_supplement_appends_to_existing_case():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "intent_result": {"intent": "deposit_missing"},
            "workflow_stage": "waiting_backend",
            "slot_memory": {"telegram_case_id": "tg:123", "telegram_message_id": 123},
            "attachments": [
                {
                    "url": "https://cdn.example/supplement.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "deposit",
                }
            ],
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"receipt_screenshot": "https://cdn.example/supplement.png"},
                "slot_confidence": {"receipt_screenshot": 0.95},
                "reason": "screenshot supplement",
            },
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert state["commands"][0]["payload"]["supplement"]["attachment_urls"] == ["https://cdn.example/supplement.png"]


def test_waiting_backend_image_analysis_receipt_supplement_appends_to_existing_case():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "intent_result": {"intent": "deposit_missing"},
            "workflow_stage": "waiting_backend",
            "slot_memory": {"telegram_case_id": "tg:123", "telegram_message_id": 123},
            "attachments": [
                {
                    "url": "https://cdn.example/supplement.png",
                    "content_type": "image/png",
                    "image_analysis_status": "analyzed",
                    "image_analysis": {
                        "is_receipt_like": True,
                        "receipt_kind": "deposit",
                        "confidence": 0.91,
                    },
                }
            ],
            "llm_sop_dialogue_plan": {"status": "fallback", "fallback_reason": "missing_provider"},
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert state["commands"][0]["payload"]["supplement"]["attachment_urls"] == ["https://cdn.example/supplement.png"]


def test_waiting_backend_current_workflow_resolution_acknowledges_without_tg_append():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "intent_result": {
                "intent": "deposit_missing",
                "workflow_relation": "current_workflow_resolution",
                "preserve_active_workflow": False,
            },
            "workflow_stage": "waiting_backend",
            "reply_language": "es",
            "slot_memory": {"telegram_case_id": "tg:123", "telegram_message_id": 123},
            "attachments": [],
            "raw_user_input": "Gracias.. ya llegó el depósito",
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_workflow_resolution",
                "slot_updates": {},
                "slot_confidence": {},
                "reason": "customer confirms deposit arrived",
            },
        }
    )

    assert state["commands"] == []
    assert state["sop_action"] == "customer_confirmed_resolved"
    assert state["workflow_stage"] == "completed"
    assert state["active_workflow"] is None
    assert state["response_text"] == "Gracias por avisarnos. Me alegra saber que ya llegó. Si necesitas ayuda con algo más, puedes escribirme aquí."
