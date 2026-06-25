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
    if intent == "deposit_howto":
        return _deposit_howto_sop(state)
    if intent == "withdrawal_howto":
        return _withdrawal_howto_sop(state)
    if intent == "forgot_password":
        return _forgot_password_sop(state)
    if intent == "pending_reply_lookup":
        return _pending_reply_lookup_sop(state)
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


def _deposit_howto_sop(state: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "response_text": "充值教程：请在平台内进入充值页面，按页面提示选择可用方式并提交。若支付后需要协助，请提供用户名或注册手机号及付款截图。",
        "commands": [],
    }


def _withdrawal_howto_sop(state: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "response_text": "提款教程：请在平台内进入提款页面，按页面提示提交提款申请。若提款遇到限制或长时间未收到，请提供用户名或注册手机号及提款截图。",
        "commands": [],
    }


def _forgot_password_sop(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "").lower()
    still_blocked = any(token in text for token in ("还是不行", "仍无法", "无法登录", "no puedo", "sigue", "still", "not working"))
    if slot_memory.get("forgot_password_tutorial_sent") and still_blocked:
        return {
            **state,
            "slot_memory": slot_memory,
            "status": "HANDOFF_REQUESTED",
            "response_text": "我会为你转接真人客服继续协助登录问题。",
            "commands": [
                {
                    "type": CommandType.HUMAN_HANDOFF_REQUESTED,
                    "payload": {"reason": "forgot_password_still_blocked"},
                }
            ],
        }

    slot_memory["forgot_password_tutorial_sent"] = True
    return {
        **state,
        "slot_memory": slot_memory,
        "active_workflow": "forgot_password",
        "workflow_stage": "tutorial_sent",
        "response_text": "忘记密码教程：请在登录页面点击忘记密码，按页面提示完成验证并重设密码。若仍无法登录，请告诉我，我会为你转接真人客服。",
        "commands": [],
    }


def _pending_reply_lookup_sop(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    signal = state.get("signal_result") or {}
    if signal.get("has_identity") and signal.get("identity_value"):
        slot_memory["pending_reply_identity"] = signal["identity_value"]

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
