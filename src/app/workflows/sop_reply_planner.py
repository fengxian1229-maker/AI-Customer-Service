from typing import Any


def plan_sop_reply(intent: str, policy_result: dict[str, Any], language: str | None = None) -> dict[str, str]:
    del language
    action = policy_result.get("action")
    missing = set(policy_result.get("missing_slots") or [])
    if action == "ask_missing_slots":
        if "account_or_phone" in missing and ("deposit_screenshot" in missing or "withdrawal_screenshot" in missing):
            screenshot = "存款付款截图" if intent == "deposit_missing" else "提款截图"
            return {"reply_text": f"请提供用户名或注册手机号，并上传{screenshot}。", "next_step": "wait_customer_slot"}
        if "account_or_phone" in missing:
            prefix = "已收到提款截图" if intent == "withdrawal_missing" else "已收到截图"
            return {"reply_text": f"{prefix}，请再提供用户名或注册手机号。", "next_step": "wait_customer_slot"}
        if "deposit_screenshot" in missing:
            return {"reply_text": "收到，请上传存款付款截图。", "next_step": "wait_customer_slot"}
        if "withdrawal_screenshot" in missing:
            return {"reply_text": "收到，请上传提款申请截图。", "next_step": "wait_customer_slot"}
        return {"reply_text": "请补充必要资料，我们会继续协助。", "next_step": "wait_customer_slot"}
    if action == "send_telegram_case":
        return {"reply_text": "已为您转交后台确认，请稍等。", "next_step": "wait_backend"}
    if action == "append_to_case":
        return {"reply_text": "已补充给后台继续确认，请稍等。", "next_step": "wait_backend"}
    if action == "waiting_followup":
        return {"reply_text": "案件仍在确认中，有更新会在这里通知你。", "next_step": "wait_backend"}
    if action == "human_handoff":
        return {"reply_text": "我会为你转接真人客服继续协助。", "next_step": "human_handoff"}
    return {"reply_text": "请稍等，我们会继续协助。", "next_step": "unknown"}
