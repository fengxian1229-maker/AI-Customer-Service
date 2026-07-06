from app.workflows.command_contracts import CommandType
from app.workflows.sop_handlers import run_sop


def assert_deposit_missing_intro_commands(commands):
    assert [command["type"] for command in commands] == [
        CommandType.LIVECHAT_SEND_IMAGE,
        CommandType.LIVECHAT_SEND_TEXT,
        CommandType.LIVECHAT_SEND_TEXT,
    ]


def test_deposit_missing_asks_for_identity_and_screenshot_when_empty():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["response_text"] == "上方图片是付款成功截图示例。为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。"
    assert_deposit_missing_intro_commands(state["commands"])
    assert state["commands"][0]["payload"]["asset_key"] == "deposit_payment_success_example"
    assert state["commands"][0]["payload"]["asset_ref"].endswith("bot66tornado/assets/examples/deposit-payment-success-onepay.jpg")
    assert "存款未到账通常可能" in state["commands"][1]["payload"]["text"]
    assert state["commands"][1]["payload"]["final_reply_exempt"] is True
    assert state["commands"][2]["payload"] == {
        "text": "上方图片是付款成功截图示例。为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。",
        "final_reply_target": True,
    }
    assert state["slot_memory"]["deposit_missing_example_sent"] is True
    assert state["node_reply_template"] == "sop_missing_slots"


def test_deposit_missing_ignores_llm_missing_slot_reply_draft_for_stable_prompt():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {},
            "attachments": [],
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "slot_updates": {},
                "slot_confidence": {},
                "reply_draft": "了解，请问您的注册手机号是多少？同时请上传一张存款成功的付款截图，以便为您查询。",
            },
        }
    )

    assert state["response_text"] == "上方图片是付款成功截图示例。为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。"
    assert_deposit_missing_intro_commands(state["commands"])


def test_deposit_missing_generates_case_card_when_identity_and_screenshot_complete():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {},
            "attachments": [
                {
                    "url": "https://cdn.example/deposit.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "deposit",
                }
            ],
        }
    )

    assert state["status"] == "WAITING_EXTERNAL"
    assert state["active_workflow"] == "deposit_missing"
    assert state["workflow_stage"] == "waiting_backend"
    assert state["response_text"] == "资料已收到，我们现在为你查询这笔存款，请稍等。"
    assert "转交后台" not in state["response_text"]
    assert "提交后台" not in state["response_text"]
    assert "同步给后台" not in state["response_text"]
    assert "已到账" not in state["response_text"]
    assert "保证" not in state["response_text"]
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
    assert_deposit_missing_intro_commands(state["commands"])
    assert state["slot_memory"]["deposit_order_id"] == "D123456"
    assert state["slot_memory"]["amount"] == "1000"
    assert state["slot_memory"]["channel"] == "GCASH"
    assert state["response_text"] == "上方图片是付款成功截图示例。为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。"


def test_deposit_missing_with_example_already_sent_only_asks_for_missing_data():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {"deposit_missing_example_sent": True},
            "attachments": [],
        }
    )

    assert state["response_text"] == "前面那张图片是付款成功截图示例。为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。"
    assert state["commands"] == []


def test_deposit_missing_with_example_already_sent_and_identity_only_explains_example_image():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {"deposit_missing_example_sent": True},
            "attachments": [],
        }
    )

    assert state["response_text"] == "前面那张图片只是示例，请上传你自己的存款付款成功截图，我们会继续帮你查询。"
    assert state["commands"] == []
    assert "示例" in state["reply_plan"]["must_say"]
    assert "马上到账" in state["reply_plan"]["must_not_say"]


def test_deposit_missing_with_identity_only_sends_three_part_sop():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "rewritten_question": "mi usuario es andy123",
            "slot_memory": {},
            "attachments": [],
            "reply_language": "es",
        }
    )

    assert state["response_text"] == "上方图片只是示例，请上传你自己的存款付款成功截图，我们会继续帮你查询。"
    assert_deposit_missing_intro_commands(state["commands"])
    assert "Un depósito puede no acreditarse" in state["commands"][1]["payload"]["text"]
    assert state["commands"][2]["payload"]["text"] == "上方图片只是示例，请上传你自己的存款付款成功截图，我们会继续帮你查询。"


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
            "attachments": [
                {
                    "url": "https://cdn.example/withdrawal.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "withdrawal",
                }
            ],
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
            "attachments": [
                {
                    "url": "https://cdn.example/withdrawal.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "withdrawal",
                }
            ],
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


def test_withdrawal_blocked_or_rollover_generates_waiting_reply_and_backend_query_no_tg():
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
    assert state["response_text"] is None
    assert state["response_text_fallback"] is None
    assert state["customer_reply"]["intent"] == "backend_query_waiting"
    assert state["node_reply_template"] == "backend_waiting"
    assert state["node_facts"]["sop_name"] == "withdrawal_blocked_or_rollover"
    assert state["reply_plan"]["kind"] == "backend_waiting"


def test_withdrawal_blocked_or_rollover_nequi_is_payment_channel_not_account_and_replies_spanish():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_blocked_or_rollover"},
            "rewritten_question": "Es q no me deja retirar ya agregué mi nequi y dice q tengo q agregar otra cuenta",
            "reply_language": "es",
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["commands"] == []
    assert state["slot_memory"]["payment_channel"] == "NEQUI"
    assert "account_or_phone" not in state["slot_memory"]
    assert state["customer_reply"]["intent"] == "wallet_change_needs_human_or_clarification"
    assert "usuario o teléfono registrado" in state["response_text"]


def test_withdrawal_blocked_or_rollover_missing_account_sets_sop_template_facts():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_blocked_or_rollover"},
            "rewritten_question": "我无法提款",
            "slot_memory": {},
            "attachments": [],
        }
    )

    assert state["workflow_stage"] == "collecting_slots"
    assert state["commands"] == []
    assert state["node_reply_template"] == "sop_missing_slots"
    assert state["node_facts"]["missing_slots"] == ["account_or_phone"]


def test_withdrawal_blocked_or_rollover_has_sop_definition_for_account_lookup():
    from app.workflows.llm_sop_dialogue_planner import compute_missing_slots
    from app.workflows.sop_definitions import get_sop_definition

    definition = get_sop_definition("withdrawal_blocked_or_rollover")

    assert definition is not None
    assert definition.complete_action == "backend.query"
    assert definition.required_slots == ("account_or_phone",)
    assert compute_missing_slots("withdrawal_blocked_or_rollover", {}) == ["account_or_phone"]
    assert compute_missing_slots("withdrawal_blocked_or_rollover", {"account_or_phone": "andy123"}) == []


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
    assert_deposit_missing_intro_commands(state["commands"])


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


def test_llm_username_and_phone_preserves_username_account_slot():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "raw_user_input": "我说错了用户名是frank，手机号是12335",
            "slot_memory": {},
            "llm_sop_dialogue_plan": {
                "status": "accepted",
                "intent_relation": "current_sop_supplement",
                "slot_updates": {"account_or_phone": "frank", "phone": "12335"},
                "slot_confidence": {"account_or_phone": 0.96, "phone": 0.97},
                "reason": "username and phone supplied",
            },
        }
    )

    assert state["slot_memory"]["account_or_phone"] == "frank"
    assert state["slot_memory"]["phone"] == "12335"


def test_unverified_attachment_without_text_is_not_receipt_screenshot():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {"phone": "13800138000"},
            "attachments": [{"url": "https://cdn.example/receipt.png"}],
        }
    )

    assert "receipt_screenshot" not in state["slot_memory"]
    assert "deposit_screenshot" not in state["slot_memory"]
    assert_deposit_missing_intro_commands(state["commands"])


def test_deposit_missing_accepts_matching_image_analysis_receipt_and_asks_phone_only():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {},
            "attachments": [
                {
                    "url": "https://cdn.example/deposit.png",
                    "content_type": "image/png",
                    "image_analysis_status": "analyzed",
                    "image_analysis": {
                        "is_receipt_like": True,
                        "receipt_kind": "deposit",
                        "confidence": 0.91,
                    },
                }
            ],
        }
    )

    assert state["slot_memory"]["deposit_screenshot"] == "https://cdn.example/deposit.png"
    assert state["slot_memory"]["receipt_screenshot"] == "https://cdn.example/deposit.png"
    assert state["missing_slots"] == ["phone"]
    assert state["commands"] == []
    assert state["response_text"] == "已收到你的付款截图，请再提供用户名或注册手机号，方便我们为你查询。"


def test_deposit_missing_sends_case_when_phone_then_image_analysis_receipt():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "slot_memory": {"phone": "13800138000"},
            "attachments": [
                {
                    "url": "https://cdn.example/deposit.png",
                    "content_type": "image/png",
                    "image_analysis_status": "analyzed",
                    "image_analysis": {
                        "is_receipt_like": True,
                        "receipt_kind": "deposit",
                        "confidence": 0.91,
                    },
                }
            ],
        }
    )

    assert state["missing_slots"] == []
    assert state["slot_memory"]["deposit_screenshot"] == "https://cdn.example/deposit.png"
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_deposit_missing_sends_case_when_image_analysis_receipt_then_phone():
    state = run_sop(
        {
            "intent_result": {"intent": "deposit_missing"},
            "raw_user_input": "13800138000",
            "slot_memory": {
                "receipt_screenshot": "https://cdn.example/deposit.png",
                "deposit_screenshot": "https://cdn.example/deposit.png",
            },
            "attachments": [],
        }
    )

    assert state["missing_slots"] == []
    assert state["slot_memory"]["phone"] == "13800138000"
    assert state["commands"][0]["type"] == CommandType.TELEGRAM_SEND_CASE_CARD


def test_deposit_missing_rejects_unknown_low_confidence_and_wrong_kind_image_analysis():
    cases = [
        {"is_receipt_like": False, "receipt_kind": "deposit", "confidence": 0.95},
        {"is_receipt_like": True, "receipt_kind": "unknown", "confidence": 0.95},
        {"is_receipt_like": True, "receipt_kind": "deposit", "confidence": 0.2},
        {"is_receipt_like": True, "receipt_kind": "withdrawal", "confidence": 0.95},
    ]

    for analysis in cases:
        state = run_sop(
            {
                "intent_result": {"intent": "deposit_missing"},
                "slot_memory": {"phone": "13800138000"},
                "attachments": [
                    {
                        "url": "https://cdn.example/image.png",
                        "content_type": "image/png",
                        "image_analysis_status": "analyzed",
                        "image_analysis": analysis,
                    }
                ],
            }
        )

        assert "receipt_screenshot" not in state["slot_memory"]
        assert "deposit_screenshot" not in state["slot_memory"]
        assert_deposit_missing_intro_commands(state["commands"])


def test_mismatched_verified_attachment_is_not_receipt_screenshot():
    state = run_sop(
        {
            "intent_result": {"intent": "withdrawal_missing"},
            "slot_memory": {"phone": "13800138000"},
            "attachments": [
                {
                    "url": "https://cdn.example/deposit.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "deposit",
                }
            ],
        }
    )

    assert "receipt_screenshot" not in state["slot_memory"]
    assert "withdrawal_screenshot" not in state["slot_memory"]
    assert state["commands"] == []


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
            "attachments": [
                {
                    "url": "https://cdn.example/receipt.png",
                    "verified_receipt_attachment": True,
                    "receipt_kind": "deposit",
                }
            ],
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
    assert_deposit_missing_intro_commands(state["commands"])
