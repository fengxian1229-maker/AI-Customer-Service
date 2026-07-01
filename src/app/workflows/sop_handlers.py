from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.llm_sop_dialogue_planner import plan_sop_dialogue_from_state
from app.workflows.slot_extractors import extract_identity
from app.workflows.sop_command_builder import build_sop_command
from app.workflows.sop_policy import evaluate_sop_policy
from app.workflows.sop_reply_planner import plan_sop_reply


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
    reply = plan_sop_reply(intent, policy)
    if policy["action"] == "ask_missing_slots" and _safe_reply_draft(dialogue_plan.get("reply_draft")):
        reply = {"reply_text": dialogue_plan["reply_draft"], "next_step": "wait_customer_slot"}
    commands: list[dict[str, Any]] = []
    if policy["action"] == "send_telegram_case":
        commands.append(build_sop_command(CommandType.TELEGRAM_SEND_CASE_CARD, state, intent, slot_memory))

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
        },
        "response_text": reply["reply_text"],
        "response_text_fallback": reply["reply_text"],
        "reply_plan": _build_sop_reply_plan(intent, policy, reply["reply_text"]),
        "commands": commands,
    }
    if commands:
        next_state.update(
            {"status": "WAITING_EXTERNAL", "active_workflow": intent, "workflow_stage": "waiting_backend"}
        )
    else:
        next_state.update({"active_workflow": intent, "workflow_stage": "collecting_slots"})
    return next_state


def _withdrawal_blocked_sop(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    identity = extract_identity(text)
    if identity:
        slot_memory["account_or_phone"] = identity["value"]

    if not slot_memory.get("account_or_phone"):
        return {
            **state,
            "slot_memory": slot_memory,
            "active_workflow": "withdrawal_blocked_or_rollover",
            "workflow_stage": "collecting_slots",
            "response_text": "一般无法提款通常与流水要求或风控限制有关。为了帮你继续查询，请提供用户名或注册手机号。",
            "response_text_fallback": "一般无法提款通常与流水要求或风控限制有关。为了帮你继续查询，请提供用户名或注册手机号。",
            "reply_plan": build_reply_plan(
                kind="ask_missing_slots",
                fallback_text="一般无法提款通常与流水要求或风控限制有关。为了帮你继续查询，请提供用户名或注册手机号。",
                must_say=["流水要求", "用户名或注册手机号"],
                semantic_required_items=["account_or_phone"],
                must_not_say=["已到账", "已完成", "保证", "已处理"],
                missing_slots=["account_or_phone"],
                allowed_facts=["无法提款通常与流水要求或风控限制有关", "需要客户提供识别资料"],
            ),
            "commands": [],
        }

    return {
        **state,
        "slot_memory": slot_memory,
        "status": "WAITING_EXTERNAL",
        "active_workflow": "withdrawal_blocked_or_rollover",
        "workflow_stage": "backend_querying",
        "response_text": "一般无法提款通常与流水要求或风控限制有关。已收到你的资料，我们正在进一步查询。",
        "response_text_fallback": "一般无法提款通常与流水要求或风控限制有关。已收到你的资料，我们正在进一步查询。",
        "reply_plan": build_reply_plan(
            kind="backend_waiting",
            fallback_text="一般无法提款通常与流水要求或风控限制有关。已收到你的资料，我们正在进一步查询。",
            must_say=["正在进一步查询"],
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已到账", "已完成", "保证", "马上到账", "一定"],
            allowed_facts=["已收到客户提供的识别资料", "正在进一步查询"],
        ),
        "commands": [
            {
                "type": CommandType.BACKEND_QUERY,
                "payload": {
                    "intent": "withdrawal_blocked_or_rollover",
                    "account_or_phone": slot_memory["account_or_phone"],
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
                "payload": {"pending_reply_identity": slot_memory["pending_reply_identity"]},
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
        if "withdrawal_screenshot" in missing_slots or ("receipt_screenshot" in missing_slots and intent == "withdrawal_missing"):
            must_say.append("提款")
        return build_reply_plan(
            kind="ask_missing_slots",
            fallback_text=fallback_text,
            must_say=must_say,
            semantic_required_items=missing_slots,
            must_not_say=["已到账", "已完成", "已处理", "保证"],
            missing_slots=missing_slots,
            allowed_facts=["需要客户补充资料"],
            metadata={"intent": intent, "sop_action": action},
        )
    if action == "send_telegram_case":
        return build_reply_plan(
            kind="send_backend_case",
            fallback_text=fallback_text,
            must_say=["后台确认", "请稍等"],
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已到账", "已完成", "已处理", "保证", "马上到账"],
            allowed_facts=["已转交后台确认"],
            metadata={"intent": intent, "sop_action": action},
        )
    if action == "append_to_case":
        return build_reply_plan(
            kind="append_backend_case",
            fallback_text=fallback_text,
            must_say=["后台", "请稍等"],
            semantic_required_items=["backend_waiting_notice"],
            must_not_say=["已到账", "已完成", "已处理", "保证", "马上到账"],
            allowed_facts=["已补充给后台继续确认"],
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


def _safe_reply_draft(reply_draft: Any) -> bool:
    text = str(reply_draft or "")
    forbidden = ("已到账", "到账成功", "已完成", "保证", "已处理", "credited", "completed", "guarantee")
    return bool(text.strip()) and not any(phrase.lower() in text.lower() for phrase in forbidden)
