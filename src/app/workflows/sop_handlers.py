from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.llm_sop_dialogue_planner import plan_sop_dialogue_from_state
from app.services.reply_intents import CustomerReplyIntent, build_customer_reply
from app.services.reply_renderer import render_customer_reply
from app.workflows.slot_extractors import (
    explicit_phone_reference,
    extract_channel,
    extract_identity,
    extract_identity_from_texts,
    is_wallet_or_receiving_account_change,
)
from app.workflows.sop_command_builder import build_sop_command
from app.workflows.sop_policy import evaluate_sop_policy
from app.workflows.sop_reply_planner import plan_sop_reply


DEPOSIT_PAYMENT_SUCCESS_EXAMPLE_ASSET = "bot66tornado/assets/examples/deposit-payment-success-onepay.jpg"
DEPOSIT_PAYMENT_SUCCESS_EXAMPLE_KEY = "deposit_payment_success_example"
DEPOSIT_EXAMPLE_SENT_SLOT = "deposit_missing_example_sent"

DEPOSIT_MISSING_REASON_TEXT: dict[str, str] = {
    "zh-Hans": "存款未到账通常可能是付款还未成功、付款凭证信息与平台订单不一致、渠道处理延迟，或截图资料不足导致暂时无法核实。",
    "zh-Hant": "存款未到帳通常可能是付款尚未成功、付款憑證資訊與平台訂單不一致、渠道處理延遲，或截圖資料不足導致暫時無法核實。",
    "en": "A deposit may not be credited yet if the payment has not completed, the receipt details do not match the platform order, the payment channel is delayed, or the screenshot details are not enough to verify it.",
    "es": "Un depósito puede no acreditarse todavía si el pago no se completó, los datos del comprobante no coinciden con la orden de la plataforma, el canal de pago está demorando o la captura no tiene información suficiente para verificarlo.",
    "tl": "Maaaring hindi pa pumapasok ang deposit kung hindi pa kumpleto ang bayad, hindi tugma ang detalye ng resibo sa order sa platform, may delay sa payment channel, o kulang ang detalye sa screenshot para ma-verify ito.",
    "th": "เงินฝากอาจยังไม่เข้าหากการชำระเงินยังไม่สำเร็จ รายละเอียดในสลิปไม่ตรงกับคำสั่งซื้อของแพลตฟอร์ม ช่องทางชำระเงินล่าช้า หรือรายละเอียดในภาพไม่เพียงพอสำหรับการตรวจสอบ",
    "my": "ငွေသွင်းမှု မရောက်သေးခြင်းသည် ငွေပေးချေမှု မပြီးဆုံးသေးခြင်း၊ ပြေစာအချက်အလက်များသည် ပလက်ဖောင်းအော်ဒါနှင့် မကိုက်ညီခြင်း၊ ငွေပေးချေမှုချန်နယ် နှောင့်နှေးခြင်း၊ သို့မဟုတ် screenshot အချက်အလက် မလုံလောက်ခြင်းကြောင့် ဖြစ်နိုင်ပါသည်။",
    "ms": "Deposit mungkin belum dikreditkan jika bayaran belum selesai, butiran resit tidak sepadan dengan pesanan platform, saluran pembayaran mengalami kelewatan, atau butiran tangkapan skrin tidak mencukupi untuk pengesahan.",
}


def run_sop(state: dict[str, Any]) -> dict[str, Any]:
    intent = (state.get("intent_result") or {}).get("intent")
    if intent == "deposit_missing":
        return _money_missing_sop(state, intent="deposit_missing", screenshot_key="deposit_screenshot")
    if intent == "withdrawal_missing":
        return _money_missing_sop(state, intent="withdrawal_missing", screenshot_key="withdrawal_screenshot")
    if intent == "withdrawal_blocked_or_rollover":
        return _withdrawal_blocked_sop(state)
    if intent == "pending_reply_lookup":
        return _pending_reply_lookup_sop(state)
    return {
        **state,
        "response_text": "请补充你要咨询的问题，我们会继续协助。",
        "response_text_fallback": "请补充你要咨询的问题，我们会继续协助。",
        "node_reply_template": "clarification",
        "node_facts": {"fallback_text": "请补充你要咨询的问题，我们会继续协助。"},
        "reply_plan": build_reply_plan(
            kind="clarification",
            fallback_text="请补充你要咨询的问题，我们会继续协助。",
            must_say=["补充", "继续协助"],
            must_not_say=["已到账", "已完成", "已处理"],
            allowed_facts=["需要客户补充问题"],
        ),
        "commands": state.get("commands", []),
    }


def _money_missing_sop(state: dict[str, Any], intent: str, screenshot_key: str) -> dict[str, Any]:
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    dialogue_plan = plan_sop_dialogue_from_state(state, intent)
    slot_memory = dialogue_plan["slot_memory"]
    policy = evaluate_sop_policy(
        intent,
        slot_memory,
        conversation_status=state.get("status"),
        active_workflow=state.get("active_workflow") or intent,
        workflow_stage="collecting_slots",
        latest_text=text,
        attachments=state.get("attachments", []),
    )
    example_will_be_sent = _should_send_deposit_missing_intro(intent, policy, slot_memory)
    policy_context = {
        **policy,
        "reply_language": state.get("reply_language") or state.get("conversation_language") or state.get("detected_language"),
        "deposit_example_sent": bool(slot_memory.get(DEPOSIT_EXAMPLE_SENT_SLOT)),
        "deposit_example_will_be_sent": example_will_be_sent,
    }
    reply = plan_sop_reply(intent, policy_context, language=policy_context.get("reply_language"))
    commands: list[dict[str, Any]] = []
    if policy["action"] == "send_telegram_case":
        commands.append(build_sop_command(CommandType.TELEGRAM_SEND_CASE_CARD, state, intent, slot_memory))
    elif example_will_be_sent:
        commands.extend(_deposit_missing_intro_commands(state, reply["reply_text"]))
        slot_memory[DEPOSIT_EXAMPLE_SENT_SLOT] = True

    next_state = {
        **state,
        "slot_memory": slot_memory,
        "missing_slots": policy.get("missing_slots", []),
        "sop_action": policy["action"],
        "llm_sop_dialogue_plan": {
            "status": dialogue_plan.get("status"),
            "source": dialogue_plan.get("source"),
            "intent_relation": dialogue_plan.get("intent_relation"),
            "slot_updates": dialogue_plan.get("slot_updates"),
            "slot_confidence": dialogue_plan.get("slot_confidence"),
            "missing_slots": dialogue_plan.get("missing_slots"),
            "should_ask_confirmation": dialogue_plan.get("should_ask_confirmation"),
            "reply_draft": dialogue_plan.get("reply_draft"),
            "reason": dialogue_plan.get("reason"),
            "dropped_slots": dialogue_plan.get("dropped_slots"),
            "fallback_reason": dialogue_plan.get("fallback_reason"),
            "error_type": dialogue_plan.get("error_type"),
            "error_message": dialogue_plan.get("error_message"),
        },
        "response_text": reply["reply_text"],
        "response_text_fallback": reply["reply_text"],
        "node_reply_template": _sop_node_reply_template(policy["action"], has_external_command=_has_external_sop_command(commands)),
        "node_facts": {
            "sop_name": intent,
            "sop_action": policy["action"],
            "missing_slots": list(policy.get("missing_slots") or []),
            "slot_memory": slot_memory,
            "fallback_text": reply["reply_text"],
        },
        "reply_plan": _build_sop_reply_plan(intent, policy_context, reply["reply_text"]),
        "commands": commands,
    }
    if _has_external_sop_command(commands):
        next_state.update(
            {"status": "WAITING_EXTERNAL", "active_workflow": intent, "workflow_stage": "waiting_backend"}
        )
    else:
        next_state.update({"active_workflow": intent, "workflow_stage": "collecting_slots"})
    return next_state


def _withdrawal_blocked_sop(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    raw_text = str(state.get("raw_user_input") or "")
    rewritten_text = str(state.get("rewritten_question") or "")
    text = rewritten_text or raw_text
    reply_language = state.get("reply_language") or state.get("conversation_language") or state.get("detected_language")
    identity = extract_identity_from_texts(raw_text, rewritten_text)
    if identity:
        slot_memory["account_or_phone"] = identity["value"]
        slot_memory["identity_kind"] = identity["type"]
    elif slot_memory.get("account_or_phone") and explicit_phone_reference(raw_text):
        slot_memory["identity_kind"] = "phone"
    payment_channel = extract_channel(text)
    if payment_channel:
        slot_memory["payment_channel"] = payment_channel

    if is_wallet_or_receiving_account_change(text) and not identity:
        reply_intent = CustomerReplyIntent.WALLET_CHANGE_NEEDS_HUMAN_OR_CLARIFICATION
        reply_facts = {"payment_channel": payment_channel or slot_memory.get("payment_channel") or "wallet"}
        fallback_text = render_customer_reply(reply_intent, facts=reply_facts, reply_language=reply_language)
        return {
            **state,
            "slot_memory": slot_memory,
            "active_workflow": "withdrawal_blocked_or_rollover",
            "workflow_stage": "collecting_slots",
            "response_text": fallback_text,
            "response_text_fallback": fallback_text,
            "customer_reply": build_customer_reply(reply_intent, facts=reply_facts, language=reply_language, text=fallback_text),
            "node_reply_template": "sop_missing_slots",
            "node_facts": {
                "sop_name": "withdrawal_blocked_or_rollover",
                "missing_slots": ["account_or_phone"],
                "slot_memory": slot_memory,
                "reply_intent": str(reply_intent),
                "reply_facts": reply_facts,
                "fallback_text": fallback_text,
            },
            "reply_plan": build_reply_plan(
                kind="ask_missing_slots",
                fallback_text=fallback_text,
                semantic_required_items=["account_or_phone"],
                must_not_say=["已到账", "已完成", "保证", "已处理"],
                missing_slots=["account_or_phone"],
                allowed_facts=["客户提到钱包或收款方式", "需要客户提供识别资料或确认是否更换收款账户"],
            ),
            "commands": [],
        }

    if not slot_memory.get("account_or_phone"):
        reply_intent = CustomerReplyIntent.ASK_ACCOUNT_OR_PHONE
        reply_facts = {}
        fallback_text = render_customer_reply(reply_intent, facts=reply_facts, reply_language=reply_language)
        return {
            **state,
            "slot_memory": slot_memory,
            "active_workflow": "withdrawal_blocked_or_rollover",
            "workflow_stage": "collecting_slots",
            "response_text": fallback_text,
            "response_text_fallback": fallback_text,
            "customer_reply": build_customer_reply(reply_intent, facts=reply_facts, language=reply_language, text=fallback_text),
            "node_reply_template": "sop_missing_slots",
            "node_facts": {
                "sop_name": "withdrawal_blocked_or_rollover",
                "missing_slots": ["account_or_phone"],
                "slot_memory": slot_memory,
                "reply_intent": str(reply_intent),
                "reply_facts": reply_facts,
                "fallback_text": fallback_text,
            },
            "reply_plan": build_reply_plan(
                kind="ask_missing_slots",
                fallback_text=fallback_text,
                semantic_required_items=["account_or_phone"],
                must_not_say=["已到账", "已完成", "保证", "已处理"],
                missing_slots=["account_or_phone"],
                allowed_facts=["无法提款通常与流水要求或风控限制有关", "需要客户提供识别资料"],
            ),
            "commands": [],
        }

    reply_intent = CustomerReplyIntent.BACKEND_QUERY_WAITING
    reply_facts = {"account_or_phone": slot_memory["account_or_phone"]}
    return {
        **state,
        "slot_memory": slot_memory,
        "status": "WAITING_EXTERNAL",
        "active_workflow": "withdrawal_blocked_or_rollover",
        "workflow_stage": "backend_querying",
        "response_text": None,
        "response_text_fallback": None,
        "customer_reply": build_customer_reply(reply_intent, facts=reply_facts, language=reply_language),
        "node_reply_template": "backend_waiting",
        "node_facts": {
            "sop_name": "withdrawal_blocked_or_rollover",
            "sop_action": "backend_query",
            "slot_memory": slot_memory,
            "reply_intent": str(reply_intent),
            "reply_facts": reply_facts,
            "fallback_text": None,
        },
        "reply_plan": build_reply_plan(
            kind="backend_waiting",
            fallback_text="",
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已完成", "已处理", "已到账", "马上到账", "保证", "一定"],
            allowed_facts=["已收到账号资料", "将查询提款限制或流水要求"],
            metadata={"intent": "withdrawal_blocked_or_rollover", "sop_action": "backend_query"},
        ),
        "commands": [
            {
                "type": CommandType.BACKEND_QUERY,
                "payload": {
                    "intent": "withdrawal_blocked_or_rollover",
                    "account_or_phone": slot_memory["account_or_phone"],
                    "identity_kind": slot_memory.get("identity_kind"),
                    "reply_language": state.get("reply_language"),
                    "conversation_language": state.get("conversation_language"),
                    "detected_language": state.get("detected_language"),
                    "raw_user_input": state.get("raw_user_input"),
                    "rewritten_question": state.get("rewritten_question"),
                },
            }
        ],
    }


def _pending_reply_lookup_sop(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    identity = extract_identity(text)
    if identity:
        slot_memory["pending_reply_identity"] = identity["value"]

    if not slot_memory.get("pending_reply_identity"):
        return {
            **state,
            "slot_memory": slot_memory,
            "active_workflow": "pending_reply_lookup",
            "workflow_stage": "collecting_slots",
            "response_text": "请提供可用于查询上一笔案件的识别资料，例如用户名、注册手机号或邮箱。",
            "response_text_fallback": "请提供可用于查询上一笔案件的识别资料，例如用户名、注册手机号或邮箱。",
            "node_reply_template": "sop_missing_slots",
            "node_facts": {
                "sop_name": "pending_reply_lookup",
                "missing_slots": ["pending_reply_identity"],
                "slot_memory": slot_memory,
                "fallback_text": "请提供可用于查询上一笔案件的识别资料，例如用户名、注册手机号或邮箱。",
            },
            "reply_plan": build_reply_plan(
                kind="ask_missing_slots",
                fallback_text="请提供可用于查询上一笔案件的识别资料，例如用户名、注册手机号或邮箱。",
                must_say=["用户名", "注册手机号", "邮箱"],
                semantic_required_items=["pending_reply_identity"],
                must_not_say=["已查询", "已完成", "已处理"],
                missing_slots=["pending_reply_identity"],
                allowed_facts=["需要客户提供识别资料以查询上一笔案件"],
            ),
            "commands": [],
        }

    return {
        **state,
        "slot_memory": slot_memory,
        "status": "WAITING_EXTERNAL",
        "active_workflow": "pending_reply_lookup",
        "workflow_stage": "lookup_pending_reply",
        "response_text": "已收到识别资料，我们会查询上一笔案件记录，有更新会在这里通知你。",
        "response_text_fallback": "已收到识别资料，我们会查询上一笔案件记录，有更新会在这里通知你。",
        "node_reply_template": "backend_waiting",
        "node_facts": {
            "sop_name": "pending_reply_lookup",
            "sop_action": "pending_reply_lookup",
            "slot_memory": slot_memory,
            "fallback_text": "已收到识别资料，我们会查询上一笔案件记录，有更新会在这里通知你。",
        },
        "reply_plan": build_reply_plan(
            kind="backend_waiting",
            fallback_text="已收到识别资料，我们会查询上一笔案件记录，有更新会在这里通知你。",
            must_say=["查询上一笔案件", "有更新会在这里通知你"],
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已查询完成", "已完成", "马上处理"],
            allowed_facts=["已收到识别资料", "将查询上一笔案件记录"],
        ),
        "commands": [
            {
                "type": CommandType.PENDING_REPLY_LOOKUP,
                "payload": {
                    "pending_reply_identity": slot_memory["pending_reply_identity"],
                    "reply_language": state.get("reply_language"),
                    "conversation_language": state.get("conversation_language"),
                    "detected_language": state.get("detected_language"),
                    "raw_user_input": state.get("raw_user_input"),
                    "rewritten_question": state.get("rewritten_question"),
                    "slot_memory": dict(slot_memory),
                },
            }
        ],
    }


def _build_sop_reply_plan(intent: str, policy: dict[str, Any], fallback_text: str) -> dict[str, Any]:
    action = str(policy.get("action") or "")
    missing_slots = list(policy.get("missing_slots") or [])
    if action == "ask_missing_slots":
        must_say = []
        if "account_or_phone" in missing_slots or "phone" in missing_slots:
            must_say.append("用户名或注册手机号")
        if "deposit_screenshot" in missing_slots or ("receipt_screenshot" in missing_slots and intent == "deposit_missing"):
            must_say.append("存款付款截图")
        if (
            intent == "deposit_missing"
            and ("deposit_screenshot" in missing_slots or "receipt_screenshot" in missing_slots)
            and (policy.get("deposit_example_sent") or policy.get("deposit_example_will_be_sent"))
        ):
            must_say.append("示例")
        if "withdrawal_screenshot" in missing_slots or ("receipt_screenshot" in missing_slots and intent == "withdrawal_missing"):
            must_say.append("提款")
        return build_reply_plan(
            kind="ask_missing_slots",
            fallback_text=fallback_text,
            must_say=must_say,
            semantic_required_items=missing_slots,
            must_not_say=["已到账", "已完成", "已处理", "保证", "马上到账"],
            missing_slots=missing_slots,
            allowed_facts=["需要客户补充资料"],
            metadata={"intent": intent, "sop_action": action},
        )
    if action == "send_telegram_case":
        return build_reply_plan(
            kind="send_backend_case",
            fallback_text=fallback_text,
            must_say=["查询", "请稍等"],
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已到账", "已完成", "已处理", "保证", "马上到账", "转交后台", "提交后台", "同步给后台"],
            allowed_facts=["现在为客户查询"],
            metadata={"intent": intent, "sop_action": action},
        )
    if action == "append_to_case":
        return build_reply_plan(
            kind="append_backend_case",
            fallback_text=fallback_text,
            must_say=["查询", "请稍等"],
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已到账", "已完成", "已处理", "保证", "马上到账", "转交后台", "提交后台", "同步给后台"],
            allowed_facts=["已收到客户补充信息并继续查询"],
            metadata={"intent": intent, "sop_action": action},
        )
    return build_reply_plan(
        kind="backend_waiting" if action == "waiting_followup" else "sop_reply",
        fallback_text=fallback_text,
        must_say=[],
        semantic_required_items=["backend_waiting_notice"] if action == "waiting_followup" else [],
        must_not_say=["已到账", "已完成", "保证", "已处理"],
        allowed_facts=[fallback_text],
        metadata={"intent": intent, "sop_action": action},
    )


def _sop_node_reply_template(action: str, *, has_external_command: bool) -> str:
    if has_external_command or action in {"send_telegram_case", "append_to_case", "waiting_followup"}:
        return "backend_waiting"
    if action == "ask_missing_slots":
        return "sop_missing_slots"
    return "acknowledgement"


def _has_external_sop_command(commands: list[dict[str, Any]]) -> bool:
    external_types = {
        str(CommandType.TELEGRAM_SEND_CASE_CARD),
        str(CommandType.TELEGRAM_APPEND_TO_CASE),
        str(CommandType.BACKEND_QUERY),
        str(CommandType.PENDING_REPLY_LOOKUP),
        str(CommandType.HUMAN_HANDOFF_REQUESTED),
    }
    return any(str(command.get("type")) in external_types for command in commands)


def _should_send_deposit_missing_intro(intent: str, policy: dict[str, Any], slot_memory: dict[str, Any]) -> bool:
    if intent != "deposit_missing" or policy.get("action") != "ask_missing_slots":
        return False
    if slot_memory.get(DEPOSIT_EXAMPLE_SENT_SLOT):
        return False
    missing_slots = set(policy.get("missing_slots") or [])
    return bool({"deposit_screenshot", "receipt_screenshot"} & missing_slots)


def _deposit_missing_intro_commands(state: dict[str, Any], collection_text: str) -> list[dict[str, Any]]:
    reason_text = _deposit_missing_reason_text(state.get("reply_language") or state.get("conversation_language") or state.get("detected_language"))
    text = f"{reason_text}\n\n{collection_text}"
    return [
        {
            "type": CommandType.LIVECHAT_SEND_IMAGE,
            "block_index": 0,
            "payload": {
                "asset_key": DEPOSIT_PAYMENT_SUCCESS_EXAMPLE_KEY,
                "asset_ref": DEPOSIT_PAYMENT_SUCCESS_EXAMPLE_ASSET,
                "caption": "",
                "position": "before",
            },
        },
        {
            "type": CommandType.LIVECHAT_SEND_TEXT,
            "block_index": 1,
            "payload": {
                "text": text,
                "final_reply_target": True,
            },
        },
    ]


def _deposit_missing_reason_text(language: str | None) -> str:
    return DEPOSIT_MISSING_REASON_TEXT.get(str(language or ""), DEPOSIT_MISSING_REASON_TEXT["zh-Hans"])
