from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.slot_extractors import extract_identity
from app.workflows.sop_command_builder import build_sop_command
from app.workflows.sop_policy import evaluate_sop_policy
from app.workflows.sop_reply_planner import plan_sop_reply
from app.workflows.sop_slot_extractor import extract_sop_slots


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
        "commands": state.get("commands", []),
    }


def _money_missing_sop(state: dict[str, Any], intent: str, screenshot_key: str) -> dict[str, Any]:
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    initial_slot_memory = dict(state.get("slot_memory") or {})
    extraction = extract_sop_slots(intent, initial_slot_memory, text, state.get("attachments", []))
    slot_memory = extraction["slot_memory"]
    if (state.get("llm_sop_slot_result") or {}).get("status") == "accepted":
        for key, value in initial_slot_memory.items():
            if value and key in {"account_or_phone", "amount", "payment_channel", "order_id", "deposit_screenshot", "withdrawal_screenshot"}:
                slot_memory[key] = value
    if slot_memory.get("order_id"):
        legacy_order_key = "deposit_order_id" if intent == "deposit_missing" else "withdrawal_order_id"
        slot_memory.setdefault(legacy_order_key, slot_memory["order_id"])
    if slot_memory.get("payment_channel"):
        slot_memory.setdefault("channel", slot_memory["payment_channel"])
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
    commands: list[dict[str, Any]] = []
    if policy["action"] == "send_telegram_case":
        commands.append(build_sop_command(CommandType.TELEGRAM_SEND_CASE_CARD, state, intent, slot_memory))

    next_state = {
        **state,
        "slot_memory": slot_memory,
        "missing_slots": policy.get("missing_slots", []),
        "sop_action": policy["action"],
        "response_text": reply["reply_text"],
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
            "commands": [],
        }

    return {
        **state,
        "slot_memory": slot_memory,
        "status": "WAITING_EXTERNAL",
        "active_workflow": "withdrawal_blocked_or_rollover",
        "workflow_stage": "backend_querying",
        "response_text": "一般无法提款通常与流水要求或风控限制有关。已收到你的资料，我们正在进一步查询。",
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
            "commands": [],
        }

    return {
        **state,
        "slot_memory": slot_memory,
        "status": "WAITING_EXTERNAL",
        "active_workflow": "pending_reply_lookup",
        "workflow_stage": "lookup_pending_reply",
        "response_text": "已收到识别资料，我们会查询上一笔案件记录，有更新会在这里通知你。",
        "commands": [
            {
                "type": CommandType.PENDING_REPLY_LOOKUP,
                "payload": {"pending_reply_identity": slot_memory["pending_reply_identity"]},
            }
        ],
    }
