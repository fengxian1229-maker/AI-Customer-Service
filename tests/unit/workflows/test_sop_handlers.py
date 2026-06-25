from app.workflows.command_contracts import CommandType
from app.workflows.sop_handlers import run_sop


def test_deposit_missing_asks_for_identity_and_screenshot_when_empty():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "signal_result": {},
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert state["commands"] == []


def test_deposit_missing_generates_case_card_when_identity_and_screenshot_complete():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "signal_result": {"has_identity": True, "identity_value": "andy123", "identity_type": "username"},
            "slot_memory": {},
            "attachments": [{"url": "https://cdn.example/deposit.png"}],
        }
    )

    assert state["status"] == "WAITING_EXTERNAL"
    assert state["active_workflow"] == "deposit_missing"
    assert state["workflow_stage"] == "waiting_backend"
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_withdrawal_missing_with_identity_only_asks_for_screenshot():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "signal_result": {"has_identity": True, "identity_value": "andy123", "identity_type": "username"},
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["response_text"] == "收到，请上传提款申请截图。"
    assert state["commands"] == []


def test_withdrawal_missing_with_screenshot_only_asks_for_identity():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "signal_result": {},
            "slot_memory": {},
            "attachments": [{"url": "https://cdn.example/withdrawal.png"}],
        }
    )

    assert state["response_text"] == "已收到提款截图，请再提供用户名或注册手机号。"
    assert state["commands"] == []


def test_withdrawal_missing_generates_case_card_when_complete():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "signal_result": {"has_identity": True, "identity_value": "andy123", "identity_type": "username"},
            "slot_memory": {},
            "attachments": [{"url": "https://cdn.example/withdrawal.png"}],
        }
    )

    assert state["active_workflow"] == "withdrawal_missing"
    assert state["workflow_stage"] == "waiting_backend"
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_withdrawal_blocked_or_rollover_generates_backend_query_and_no_tg():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_blocked_or_rollover"},
            "signal_result": {"has_identity": True, "identity_value": "andy123", "identity_type": "username"},
            "slot_memory": {},
            "attachments": [],
        }
    )

    command_types = [command["type"] for command in state["commands"]]
    assert command_types == [CommandType.BACKEND_QUERY]
    assert CommandType.TELEGRAM_SEND_CASE_CARD not in command_types
