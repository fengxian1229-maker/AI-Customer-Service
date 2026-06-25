from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.slot_extractors import attachment_urls


def run_sop(state: dict[str, Any]) -> dict[str, Any]:
    intent = (state.get("intent_result") or {}).get("intent")
    if intent == "deposit_missing":
        return _money_missing_sop(state, intent="deposit_missing", screenshot_key="deposit_screenshot")
    if intent == "withdrawal_missing":
        return _money_missing_sop(state, intent="withdrawal_missing", screenshot_key="withdrawal_screenshot")
    if intent == "withdrawal_blocked_or_rollover":
        return _withdrawal_blocked_sop(state)
    return {
        **state,
        "response_text": "请补充你要咨询的问题，我们会继续协助。",
        "commands": state.get("commands", []),
    }


def _money_missing_sop(state: dict[str, Any], intent: str, screenshot_key: str) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    signal = state.get("signal_result") or {}
    urls = attachment_urls(state.get("attachments", []))

    if signal.get("has_identity") and signal.get("identity_value"):
        slot_memory["account_or_phone"] = signal["identity_value"]
    if urls:
        slot_memory[screenshot_key] = urls[0]
        forwarded = list(dict.fromkeys([*slot_memory.get("forwarded_attachment_urls", []), *urls]))
        slot_memory["forwarded_attachment_urls"] = forwarded

    has_identity = bool(slot_memory.get("account_or_phone"))
    has_screenshot = bool(slot_memory.get(screenshot_key))
    commands: list[dict[str, Any]] = []

    if intent == "deposit_missing":
        if not has_identity and not has_screenshot:
            response = "请提供用户名或注册手机号，并上传存款付款截图。"
        elif has_screenshot and not has_identity:
            response = "已收到存款截图，请再提供用户名或注册手机号。"
        elif has_identity and not has_screenshot:
            response = "收到，请上传付款成功截图。"
        else:
            response = "已收到你的存款案件资料，我们会继续确认，有更新会在这里通知你。"
            commands.append(_case_card_command(intent, slot_memory))
    else:
        if not has_identity and not has_screenshot:
            response = "请提供用户名或注册手机号，并上传提款截图。"
        elif has_screenshot and not has_identity:
            response = "已收到提款截图，请再提供用户名或注册手机号。"
        elif has_identity and not has_screenshot:
            response = "收到，请上传提款申请截图。"
        else:
            response = "已收到你的提款案件资料，我们会继续确认，有更新会在这里通知你。"
            commands.append(_case_card_command(intent, slot_memory))

    next_state = {
        **state,
        "slot_memory": slot_memory,
        "response_text": response,
        "commands": commands,
    }
    if commands:
        next_state.update(
            {
                "status": "WAITING_EXTERNAL",
                "active_workflow": intent,
                "workflow_stage": "waiting_backend",
            }
        )
    else:
        next_state.update({"active_workflow": intent, "workflow_stage": "collecting_slots"})
    return next_state


def _withdrawal_blocked_sop(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    signal = state.get("signal_result") or {}
    if signal.get("has_identity") and signal.get("identity_value"):
        slot_memory["account_or_phone"] = signal["identity_value"]

    if not slot_memory.get("account_or_phone"):
        return {
            **state,
            "slot_memory": slot_memory,
            "active_workflow": "withdrawal_blocked_or_rollover",
            "workflow_stage": "collecting_slots",
            "response_text": "为了帮你查询流水/提款限制，请提供用户名或注册手机号。",
            "commands": [],
        }

    return {
        **state,
        "slot_memory": slot_memory,
        "status": "WAITING_EXTERNAL",
        "active_workflow": "withdrawal_blocked_or_rollover",
        "workflow_stage": "backend_querying",
        "response_text": "已收到，我们正在查询你的流水要求。",
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


def _case_card_command(intent: str, slot_memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": CommandType.TELEGRAM_SEND_CASE_CARD,
        "payload": {
            "intent": intent,
            "slot_memory": slot_memory,
        },
    }
