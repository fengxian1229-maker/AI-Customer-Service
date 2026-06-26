from app.workflows.command_contracts import CommandType
from app.workflows.sop_handlers import run_sop


def test_deposit_missing_asks_for_identity_and_screenshot_when_empty():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
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
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {},
            "attachments": [{"url": "https://cdn.example/deposit.png"}],
        }
    )

    assert state["status"] == "WAITING_EXTERNAL"
    assert state["active_workflow"] == "deposit_missing"
    assert state["workflow_stage"] == "waiting_backend"
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_deposit_missing_does_not_generate_case_card_with_order_amount_and_channel_only():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "rewritten_question": "我的存款订单 D123456 没到账，金额 1000，渠道 GCASH",
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["workflow_stage"] == "collecting_slots"
    assert state["commands"] == []
    assert state["slot_memory"]["deposit_order_id"] == "D123456"
    assert state["slot_memory"]["amount"] == "1000"
    assert state["slot_memory"]["channel"] == "GCASH"
    assert state["response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"


def test_withdrawal_missing_with_identity_only_asks_for_screenshot():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "rewritten_question": "mi usuario es andy123",
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
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {},
            "attachments": [{"url": "https://cdn.example/withdrawal.png"}],
        }
    )

    assert state["active_workflow"] == "withdrawal_missing"
    assert state["workflow_stage"] == "waiting_backend"
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_withdrawal_missing_does_not_generate_case_card_with_order_amount_and_channel_only():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "rewritten_question": "我的提款订单 W987654 没到账，金额 500，渠道 银行卡",
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["active_workflow"] == "withdrawal_missing"
    assert state["workflow_stage"] == "collecting_slots"
    assert state["commands"] == []
    assert state["slot_memory"]["withdrawal_order_id"] == "W987654"
    assert state["slot_memory"]["amount"] == "500"
    assert state["slot_memory"]["channel"] == "银行卡"
    assert state["response_text"] == "请提供用户名或注册手机号，并上传提款截图。"


def test_withdrawal_blocked_or_rollover_generates_backend_query_and_no_tg():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_blocked_or_rollover"},
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {},
            "attachments": [],
        }
    )

    command_types = [command["type"] for command in state["commands"]]
    assert command_types == [CommandType.BACKEND_QUERY]
    assert CommandType.TELEGRAM_SEND_CASE_CARD not in command_types


def test_pending_reply_lookup_asks_identity_when_missing():
    state = run_sop(
        {
            "intent_result": {"intent": "pending_reply_lookup"},
            "slot_memory": {},
            "commands": [],
        }
    )

    assert "识别资料" in state["response_text"]
    assert state["commands"] == []


def test_pending_reply_lookup_generates_lookup_when_identity_present():
    state = run_sop(
        {
            "intent_result": {"intent": "pending_reply_lookup"},
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {},
            "commands": [],
        }
    )

    assert state["commands"][0]["type"] == CommandType.PENDING_REPLY_LOOKUP
