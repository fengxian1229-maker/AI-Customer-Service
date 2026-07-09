from typing import Any


def plan_sop_reply(intent: str, policy_result: dict[str, Any], language: str | None = None) -> dict[str, str]:
    language_key = _language_key(language)
    action = policy_result.get("action")
    missing = set(policy_result.get("missing_slots") or [])
    if action == "ask_missing_slots":
        needs_phone = bool({"account_or_phone", "phone"} & missing)
        needs_screenshot = bool({"deposit_screenshot", "withdrawal_screenshot", "receipt_screenshot"} & missing)
        if needs_phone and needs_screenshot:
            if intent == "deposit_missing":
                prefix = _deposit_example_prefix(policy_result, with_success_word=True)
                if language_key == "en":
                    return {
                        "reply_text": f"{prefix}To help check this deposit, please provide your username or registered phone number and upload your own successful payment screenshot.",
                        "next_step": "wait_customer_slot",
                    }
                if language_key == "es":
                    return {
                        "reply_text": f"{prefix}Para ayudarle a revisar este depósito, proporcione su usuario o número de teléfono registrado y suba una captura de su pago exitoso.",
                        "next_step": "wait_customer_slot",
                    }
                return {
                    "reply_text": f"{prefix}为了帮你查询这笔存款，请提供用户名或注册手机号，并上传你自己的付款成功截图。",
                    "next_step": "wait_customer_slot",
                }
            if language_key == "en":
                screenshot = "deposit payment screenshot" if intent == "deposit_missing" else "withdrawal screenshot"
                return {"reply_text": f"Please provide your username or registered phone number and upload the {screenshot}.", "next_step": "wait_customer_slot"}
            if language_key == "es":
                screenshot = "captura del pago del depósito" if intent == "deposit_missing" else "captura de la solicitud de retiro"
                return {"reply_text": f"Proporcione su usuario o número de teléfono registrado y suba la {screenshot}.", "next_step": "wait_customer_slot"}
            screenshot = "存款付款截图" if intent == "deposit_missing" else "提款截图"
            return {"reply_text": f"请提供用户名或注册手机号，并上传{screenshot}。", "next_step": "wait_customer_slot"}
        if needs_phone:
            if intent == "deposit_missing":
                if language_key == "en":
                    return {
                        "reply_text": "We have received your payment screenshot. Please also provide your username or registered phone number so we can check it for you.",
                        "next_step": "wait_customer_slot",
                    }
                if language_key == "es":
                    return {
                        "reply_text": "Hemos recibido su captura de pago. Proporcione también su usuario o número de teléfono registrado para poder revisarlo.",
                        "next_step": "wait_customer_slot",
                    }
                return {
                    "reply_text": "已收到你的付款截图，请再提供用户名或注册手机号，方便我们为你查询。",
                    "next_step": "wait_customer_slot",
                }
            if language_key == "en":
                prefix = "We have received the withdrawal screenshot" if intent == "withdrawal_missing" else "We have received the screenshot"
                return {"reply_text": f"{prefix}. Please also provide your username or registered phone number.", "next_step": "wait_customer_slot"}
            if language_key == "es":
                prefix = "Hemos recibido la captura del retiro" if intent == "withdrawal_missing" else "Hemos recibido la captura"
                return {"reply_text": f"{prefix}. Proporcione también su usuario o número de teléfono registrado.", "next_step": "wait_customer_slot"}
            prefix = "已收到提款截图" if intent == "withdrawal_missing" else "已收到截图"
            return {"reply_text": f"{prefix}，请再提供用户名或注册手机号。", "next_step": "wait_customer_slot"}
        if "deposit_screenshot" in missing or ("receipt_screenshot" in missing and intent == "deposit_missing"):
            prefix = _deposit_example_prefix(policy_result, with_success_word=False)
            if language_key == "en":
                return {
                    "reply_text": f"{prefix}Please upload your own successful deposit payment screenshot, and we will continue helping you check it.",
                    "next_step": "wait_customer_slot",
                }
            if language_key == "es":
                return {
                    "reply_text": f"{prefix}Suba su propia captura del pago exitoso del depósito y continuaremos ayudándole a revisarlo.",
                    "next_step": "wait_customer_slot",
                }
            return {
                "reply_text": f"{prefix}请上传你自己的存款付款成功截图，我们会继续帮你查询。",
                "next_step": "wait_customer_slot",
            }
        if "withdrawal_screenshot" in missing or ("receipt_screenshot" in missing and intent == "withdrawal_missing"):
            if language_key == "en":
                return {"reply_text": "Got it. Please upload a screenshot of the withdrawal request.", "next_step": "wait_customer_slot"}
            if language_key == "es":
                return {"reply_text": "Entendido. Suba una captura de la solicitud de retiro.", "next_step": "wait_customer_slot"}
            return {"reply_text": "收到，请上传提款申请截图。", "next_step": "wait_customer_slot"}
        if language_key == "en":
            return {"reply_text": "Please provide the required details so we can continue helping you.", "next_step": "wait_customer_slot"}
        if language_key == "es":
            return {"reply_text": "Proporcione los datos necesarios para que podamos seguir ayudándole.", "next_step": "wait_customer_slot"}
        return {"reply_text": "请补充必要资料，我们会继续协助。", "next_step": "wait_customer_slot"}
    if action == "send_telegram_case":
        if language_key == "en":
            return {"reply_text": "Thank you for the information. We are now checking this deposit for you. Please wait a moment.", "next_step": "wait_backend"}
        if language_key == "es":
            return {"reply_text": "Gracias por la información. Ahora estamos revisando este depósito para usted. Espere un momento.", "next_step": "wait_backend"}
        if intent == "deposit_missing":
            return {"reply_text": "资料已收到，我们现在为你查询这笔存款，请稍等。", "next_step": "wait_backend"}
        return {"reply_text": "感谢您提供的截图，我们现在为您查询，请稍等。", "next_step": "wait_backend"}
    if action == "append_to_case":
        if language_key == "en":
            return {"reply_text": "We have received your additional information and will continue checking it for you. Please wait a moment.", "next_step": "wait_backend"}
        if language_key == "es":
            return {"reply_text": "Hemos recibido su información adicional y continuaremos revisándola. Espere un momento.", "next_step": "wait_backend"}
        return {"reply_text": "已收到您的补充信息，我们继续为您查询，请稍等。", "next_step": "wait_backend"}
    if action == "waiting_followup":
        if language_key == "en":
            return {"reply_text": "Your case is still being checked. We will update you here once there is progress.", "next_step": "wait_backend"}
        if language_key == "es":
            return {"reply_text": "Su caso aún se está revisando. Le avisaremos aquí cuando haya novedades.", "next_step": "wait_backend"}
        return {"reply_text": "案件仍在确认中，有更新会在这里通知你。", "next_step": "wait_backend"}
    if action == "human_handoff":
        if language_key == "en":
            return {"reply_text": "I will request a human agent to continue assisting you.", "next_step": "human_handoff"}
        if language_key == "es":
            return {"reply_text": "Solicitaré que un agente humano continúe ayudándole.", "next_step": "human_handoff"}
        return {"reply_text": "我会为你转接真人客服继续协助。", "next_step": "human_handoff"}
    if language_key == "en":
        return {"reply_text": "Please wait a moment. We will continue assisting you.", "next_step": "unknown"}
    if language_key == "es":
        return {"reply_text": "Espere un momento. Continuaremos ayudándole.", "next_step": "unknown"}
    return {"reply_text": "请稍等，我们会继续协助。", "next_step": "unknown"}


def _deposit_example_prefix(policy_result: dict[str, Any], *, with_success_word: bool) -> str:
    language_key = _language_key(policy_result.get("reply_language"))
    if policy_result.get("deposit_example_will_be_sent"):
        if language_key == "en":
            return "The image above is an example of a successful payment screenshot. " if with_success_word else "The image above is only an example. "
        if language_key == "es":
            return "La imagen de arriba es un ejemplo de captura de pago exitoso. " if with_success_word else "La imagen de arriba es solo un ejemplo. "
        return "上方图片是付款成功截图示例。" if with_success_word else "上方图片只是示例，"
    if policy_result.get("deposit_example_sent"):
        if language_key == "en":
            return "The previous image is an example of a successful payment screenshot. " if with_success_word else "The previous image is only an example. "
        if language_key == "es":
            return "La imagen anterior es un ejemplo de captura de pago exitoso. " if with_success_word else "La imagen anterior es solo un ejemplo. "
        return "前面那张图片是付款成功截图示例。" if with_success_word else "前面那张图片只是示例，"
    return ""


def _language_key(language: str | None) -> str:
    raw = str(language or "").lower()
    if raw.startswith("en"):
        return "en"
    if raw.startswith("es"):
        return "es"
    return "zh"
