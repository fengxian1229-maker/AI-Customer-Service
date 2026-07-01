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
    assert state["response_text"] == "案件仍在建立或确认中，我们会继续跟进，请稍候。"
    assert state["slot_memory"]["forwarded_attachment_urls"] == ["https://cdn.example/supplement.png"]


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
            "attachments": [{"url": "https://cdn.example/supplement.png"}],
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
            "attachments": [{"url": "https://cdn.example/new.png"}],
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
            "attachments": [{"url": "https://cdn.example/supplement.png"}],
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
