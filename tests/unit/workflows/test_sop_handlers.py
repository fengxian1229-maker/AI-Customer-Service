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


def test_deposit_howto_returns_fixed_tutorial_without_facts():
    state = run_sop({"intent_result": {"intent": "deposit_howto"}, "commands": []})

    assert "充值" in state["response_text"]
    assert "到账" not in state["response_text"]


def test_withdrawal_howto_returns_fixed_tutorial_without_status_facts():
    state = run_sop({"intent_result": {"intent": "withdrawal_howto"}, "commands": []})

    assert "提款" in state["response_text"]
    assert "账户状态" not in state["response_text"]


def test_forgot_password_first_time_returns_tutorial():
    state = run_sop(
        {
            "intent_result": {"intent": "forgot_password"},
            "rewritten_question": "olvidé mi contraseña",
            "slot_memory": {},
            "commands": [],
        }
    )

    assert "忘记密码" in state["response_text"]
    assert state["commands"] == []


def test_forgot_password_followup_failure_generates_human_handoff():
    state = run_sop(
        {
            "intent_result": {"intent": "forgot_password"},
            "rewritten_question": "还是不行，无法登录",
            "slot_memory": {"forgot_password_tutorial_sent": True},
            "commands": [],
        }
    )

    assert state["commands"][0]["type"] == CommandType.HUMAN_HANDOFF_REQUESTED


def test_pending_reply_lookup_asks_identity_when_missing():
    state = run_sop(
        {
            "intent_result": {"intent": "pending_reply_lookup"},
            "signal_result": {},
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
            "signal_result": {"has_identity": True, "identity_value": "andy123", "identity_type": "username"},
            "slot_memory": {},
            "commands": [],
        }
    )

    assert state["commands"][0]["type"] == CommandType.PENDING_REPLY_LOOKUP
