from typing import Any


def plan_sop_reply(intent: str, policy_result: dict[str, Any], language: str | None = None) -> dict[str, str]:
    del language
    action = policy_result.get("action")
    missing = set(policy_result.get("missing_slots") or [])
    if action == "ask_missing_slots":
        needs_phone = bool({"account_or_phone", "phone"} & missing)
        needs_screenshot = bool({"deposit_screenshot", "withdrawal_screenshot", "receipt_screenshot"} & missing)
        if needs_phone and needs_screenshot:
            if intent == "deposit_missing":
                prefix = _deposit_example_prefix(policy_result, with_success_word=True)
                return {
                    "reply_text": f"{prefix}为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。",
                    "next_step": "wait_customer_slot",
                }
            screenshot = "存款付款截图" if intent == "deposit_missing" else "提款截图"
            return {"reply_text": f"请提供用户名或注册手机号，并上传{screenshot}。", "next_step": "wait_customer_slot"}
        if needs_phone:
            if intent == "deposit_missing":
                return {
                    "reply_text": "已收到你的付款截图，请再提供用户名或注册手机号，方便我们为你查询。",
                    "next_step": "wait_customer_slot",
                }
            prefix = "已收到提款截图" if intent == "withdrawal_missing" else "已收到截图"
            return {"reply_text": f"{prefix}，请再提供用户名或注册手机号。", "next_step": "wait_customer_slot"}
        if "deposit_screenshot" in missing or ("receipt_screenshot" in missing and intent == "deposit_missing"):
            prefix = _deposit_example_prefix(policy_result, with_success_word=False)
            return {
                "reply_text": f"{prefix}请上传你自己的存款付款成功截图，我们会继续帮你查询。",
                "next_step": "wait_customer_slot",
            }
        if "withdrawal_screenshot" in missing or ("receipt_screenshot" in missing and intent == "withdrawal_missing"):
            return {"reply_text": "收到，请上传提款申请截图。", "next_step": "wait_customer_slot"}
        return {"reply_text": "请补充必要资料，我们会继续协助。", "next_step": "wait_customer_slot"}
    if action == "send_telegram_case":
        if intent == "deposit_missing":
            return {"reply_text": "资料已收到，我们现在为你查询这笔存款，请稍等。", "next_step": "wait_backend"}
        return {"reply_text": "感谢您提供的截图，我们现在为您查询，请稍等。", "next_step": "wait_backend"}
    if action == "append_to_case":
        return {"reply_text": "已收到您的补充信息，我们继续为您查询，请稍等。", "next_step": "wait_backend"}
    if action == "waiting_followup":
        return {"reply_text": "案件仍在确认中，有更新会在这里通知你。", "next_step": "wait_backend"}
    if action == "human_handoff":
        return {"reply_text": "我会为你转接真人客服继续协助。", "next_step": "human_handoff"}
    return {"reply_text": "请稍等，我们会继续协助。", "next_step": "unknown"}


def _deposit_example_prefix(policy_result: dict[str, Any], *, with_success_word: bool) -> str:
    if policy_result.get("deposit_example_will_be_sent"):
        return "上方图片是付款成功截图示例。" if with_success_word else "上方图片只是示例，"
    if policy_result.get("deposit_example_sent"):
        return "前面那张图片是付款成功截图示例。" if with_success_word else "前面那张图片只是示例，"
    return ""
