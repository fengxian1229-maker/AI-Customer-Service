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


def test_llm_name_only_updates_customer_name_and_asks_phone_and_receipt():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {},
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"customer_name": "张三"},
                "slot_confidence": {"customer_name": 0.95},
                "missing_slots": [],
                "reason": "name supplied",
            },
        }
    )

    assert state["slot_memory"]["customer_name"] == "张三"
    assert state["missing_slots"] == ["phone", "receipt_screenshot"]
    assert state["commands"] == []


def test_llm_phone_only_updates_phone():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "raw_user_input": "这个是我的电话 13800138000",
            "slot_memory": {},
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"phone": "13800138000"},
                "slot_confidence": {"phone": 0.97},
                "reason": "phone supplied",
            },
        }
    )

    assert state["slot_memory"]["phone"] == "13800138000"
    assert state["slot_memory"]["account_or_phone"] == "13800138000"


def test_attachment_without_text_is_receipt_screenshot():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {"phone": "13800138000"},
            "attachments": [{"url": "https://cdn.example/receipt.png"}],
        }
    )

    assert state["slot_memory"]["receipt_screenshot"] == "https://cdn.example/receipt.png"
    assert state["slot_memory"]["deposit_screenshot"] == "https://cdn.example/receipt.png"


def test_llm_corrects_previous_phone():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "raw_user_input": "刚刚那个号码错了，应该是 13900001111",
            "slot_memory": {"phone": "13800138000"},
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"phone": "13900001111"},
                "slot_confidence": {"phone": 0.98},
                "reason": "phone correction",
            },
        }
    )

    assert state["slot_memory"]["phone"] == "13900001111"
    assert state["slot_memory"]["account_or_phone"] == "13900001111"


def test_llm_complete_slots_generate_telegram_case_card():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {"phone": "13800138000"},
            "attachments": [{"url": "https://cdn.example/receipt.png"}],
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"receipt_screenshot": "https://cdn.example/receipt.png"},
                "slot_confidence": {"receipt_screenshot": 0.96},
                "reason": "receipt supplied",
            },
        }
    )

    assert state["missing_slots"] == []
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_llm_unknown_and_protected_slot_keys_are_dropped():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {"telegram_message_id": 123},
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {
                    "phone": "13800138000",
                    "unknown_slot": "x",
                    "telegram_message_id": 999,
                },
                "slot_confidence": {"phone": 0.9, "unknown_slot": 0.9, "telegram_message_id": 0.9},
                "reason": "mixed slots",
            },
        }
    )

    assert state["slot_memory"]["phone"] == "13800138000"
    assert state["slot_memory"]["telegram_message_id"] == 123
    assert "unknown_slot" not in state["slot_memory"]
    assert set(state["llm_sop_dialogue_plan"]["dropped_slots"]) == {"unknown_slot", "telegram_message_id"}


def test_unsafe_llm_reply_draft_falls_back_to_safe_sop_reply():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {},
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"customer_name": "张三"},
                "slot_confidence": {"customer_name": 0.9},
                "reply_draft": "已到账，已完成，保证没问题。请给电话。",
                "reason": "unsafe draft",
            },
        }
    )

    assert "已到账" not in state["response_text"]
    assert "保证" not in state["response_text"]


def test_llm_provider_unavailable_falls_back_without_interrupting_sop():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "raw_user_input": "mi usuario es andy123",
            "slot_memory": {},
            "llm_sop_dialogue_plan": {"status": "fallback", "fallback_reason": "missing_provider"},
        }
    )

    assert state["llm_sop_dialogue_plan"]["status"] == "fallback"
    assert state["slot_memory"]["account_or_phone"] == "andy123"
    assert state["commands"] == []
