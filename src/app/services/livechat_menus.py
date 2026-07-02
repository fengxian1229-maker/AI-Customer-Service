from __future__ import annotations

import re
import unicodedata
from typing import Any


Button = dict[str, str]
Menu = dict[str, Any]


MENUS: dict[str, dict[str, Menu]] = {
    "main": {
        "es": {
            "title": "Hola, soy el bot de atención al cliente. Por favor elija su problema en los botones de abajo. Si no encuentra la opción correspondiente, elija “Otros problemas” para solicitar atención humana.",
            "buttons": [
                {"label": "💰 Problemas de depósito", "id": "deposit_menu"},
                {"label": "💸 Problemas de retiro", "id": "withdrawal_menu"},
                {"label": "🔎 Tengo un caso anterior", "id": "main_pending_reply"},
                {"label": "👤 Otros problemas", "id": "other_menu"},
            ],
        },
        "zh": {
            "title": "你好，我是客服機器人。請您在下方按鈕選單內選擇您的問題。若您找不到相對應問題，請選擇真人客服。",
            "buttons": [
                {"label": "💰 存款問題", "id": "deposit_menu"},
                {"label": "💸 提款問題", "id": "withdrawal_menu"},
                {"label": "🔎 上一筆案件", "id": "main_pending_reply"},
                {"label": "👤 其他問題", "id": "other_menu"},
            ],
        },
        "en": {
            "title": "Hello, I am the customer service bot. Please choose your issue from the buttons below. If you cannot find the right option, choose “Other issues” for live support.",
            "buttons": [
                {"label": "💰 Deposit issues", "id": "deposit_menu"},
                {"label": "💸 Withdrawal issues", "id": "withdrawal_menu"},
                {"label": "🔎 Previous case", "id": "main_pending_reply"},
                {"label": "👤 Other issues", "id": "other_menu"},
            ],
        },
    },
    "deposit": {
        "es": {
            "title": "Seleccione el caso de depósito:",
            "buttons": [
                {"label": "🧾 Depósito no acreditado", "id": "main_deposito"},
                {"label": "📘 Cómo recargar", "id": "deposit_howto"},
            ],
        },
        "zh": {
            "title": "請選擇存款問題：",
            "buttons": [
                {"label": "🧾 存款未到帳", "id": "main_deposito"},
                {"label": "📘 如何充值", "id": "deposit_howto"},
            ],
        },
        "en": {
            "title": "Choose the deposit issue:",
            "buttons": [
                {"label": "🧾 Deposit not credited", "id": "main_deposito"},
                {"label": "📘 How to deposit", "id": "deposit_howto"},
            ],
        },
    },
    "withdrawal": {
        "es": {
            "title": "Seleccione el caso de retiro:",
            "buttons": [
                {"label": "⏳ Retiro no recibido", "id": "main_retiro"},
                {"label": "🚫 No puedo retirar", "id": "withdrawal_blocked"},
                {"label": "📘 Cómo retirar", "id": "withdrawal_howto"},
                {"label": "👤 Atención humana", "id": "global_human"},
            ],
        },
        "zh": {
            "title": "請選擇提款問題：",
            "buttons": [
                {"label": "⏳ 提款未到帳", "id": "main_retiro"},
                {"label": "🚫 無法提款", "id": "withdrawal_blocked"},
                {"label": "📘 如何提款", "id": "withdrawal_howto"},
                {"label": "👤 真人客服", "id": "global_human"},
            ],
        },
        "en": {
            "title": "Choose the withdrawal issue:",
            "buttons": [
                {"label": "⏳ Withdrawal not received", "id": "main_retiro"},
                {"label": "🚫 Cannot withdraw", "id": "withdrawal_blocked"},
                {"label": "📘 How to withdraw", "id": "withdrawal_howto"},
                {"label": "👤 Live support", "id": "global_human"},
            ],
        },
    },
    "other": {
        "es": {
            "title": "Seleccione el tipo de ayuda:",
            "buttons": [
                {"label": "🔑 Olvidé mi contraseña", "id": "forgot_password"},
                {"label": "👤 Atención humana", "id": "global_human"},
            ],
        },
        "zh": {
            "title": "請選擇其他問題類型：",
            "buttons": [
                {"label": "🔑 忘記密碼", "id": "forgot_password"},
                {"label": "👤 真人客服", "id": "global_human"},
            ],
        },
        "en": {
            "title": "Choose the support type:",
            "buttons": [
                {"label": "🔑 Forgot password", "id": "forgot_password"},
                {"label": "👤 Live support", "id": "global_human"},
            ],
        },
    },
    "main_recovery": {
        "es": {
            "title": "Si esta no es la opción correcta, puede cambiar de camino:",
            "buttons": [
                {"label": "↩️ Elegir otra opción", "id": "route_previous"},
                {"label": "🏠 Menú principal", "id": "route_main"},
                {"label": "👤 Atención humana", "id": "global_human"},
            ],
        },
        "zh": {
            "title": "如果這不是你要處理的問題，可以改選：",
            "buttons": [
                {"label": "↩️ 改選其他問題", "id": "route_previous"},
                {"label": "🏠 主選單", "id": "route_main"},
                {"label": "👤 真人客服", "id": "global_human"},
            ],
        },
        "en": {
            "title": "If this is not the right option, you can change the path:",
            "buttons": [
                {"label": "↩️ Choose another option", "id": "route_previous"},
                {"label": "🏠 Main menu", "id": "route_main"},
                {"label": "👤 Live support", "id": "global_human"},
            ],
        },
    },
    "deposit_recovery": {
        "es": {
            "title": "Si esta no es la opción correcta, puede cambiar de camino:",
            "buttons": [
                {"label": "↩️ Elegir otra opción", "id": "route_previous"},
                {"label": "🏠 Menú principal", "id": "route_main"},
                {"label": "👤 Atención humana", "id": "global_human"},
            ],
        },
        "zh": {
            "title": "如果這不是你要處理的存款問題，可以改選：",
            "buttons": [
                {"label": "↩️ 改選其他問題", "id": "route_previous"},
                {"label": "🏠 主選單", "id": "route_main"},
                {"label": "👤 真人客服", "id": "global_human"},
            ],
        },
        "en": {
            "title": "If this is not the right deposit option, you can change the path:",
            "buttons": [
                {"label": "↩️ Choose another option", "id": "route_previous"},
                {"label": "🏠 Main menu", "id": "route_main"},
                {"label": "👤 Live support", "id": "global_human"},
            ],
        },
    },
    "withdrawal_recovery": {
        "es": {
            "title": "Si esta no es la opción correcta, puede cambiar de camino:",
            "buttons": [
                {"label": "↩️ Elegir otra opción", "id": "route_previous"},
                {"label": "🏠 Menú principal", "id": "route_main"},
                {"label": "👤 Atención humana", "id": "global_human"},
            ],
        },
        "zh": {
            "title": "如果這不是你要處理的提款問題，可以改選：",
            "buttons": [
                {"label": "↩️ 改選其他問題", "id": "route_previous"},
                {"label": "🏠 主選單", "id": "route_main"},
                {"label": "👤 真人客服", "id": "global_human"},
            ],
        },
        "en": {
            "title": "If this is not the right withdrawal option, you can change the path:",
            "buttons": [
                {"label": "↩️ Choose another option", "id": "route_previous"},
                {"label": "🏠 Main menu", "id": "route_main"},
                {"label": "👤 Live support", "id": "global_human"},
            ],
        },
    },
    "other_recovery": {
        "es": {
            "title": "Si esta guía no resuelve su caso, puede cambiar de camino:",
            "buttons": [
                {"label": "↩️ Elegir otra opción", "id": "route_previous"},
                {"label": "🏠 Menú principal", "id": "route_main"},
                {"label": "👤 Atención humana", "id": "global_human"},
            ],
        },
        "zh": {
            "title": "如果這個教學沒有解決你的問題，可以改選：",
            "buttons": [
                {"label": "↩️ 改選其他問題", "id": "route_previous"},
                {"label": "🏠 主選單", "id": "route_main"},
                {"label": "👤 真人客服", "id": "global_human"},
            ],
        },
        "en": {
            "title": "If this guide does not solve your case, you can change the path:",
            "buttons": [
                {"label": "↩️ Choose another option", "id": "route_previous"},
                {"label": "🏠 Main menu", "id": "route_main"},
                {"label": "👤 Live support", "id": "global_human"},
            ],
        },
    },
}


MENU_BY_NAV_BUTTON = {
    "deposit_menu": "deposit",
    "withdrawal_menu": "withdrawal",
    "other_menu": "other",
}

BUSINESS_BUTTON_ROUTES = {
    "main_deposito": {"intent": "deposit_missing", "route": "sop", "sop_name": "deposit_missing"},
    "main_retiro": {"intent": "withdrawal_missing", "route": "sop", "sop_name": "withdrawal_missing"},
    "withdrawal_blocked": {
        "intent": "withdrawal_blocked_or_rollover",
        "route": "sop",
        "sop_name": "withdrawal_blocked_or_rollover",
    },
    "main_pending_reply": {"intent": "pending_reply_lookup", "route": "sop", "sop_name": "pending_reply_lookup"},
    "deposit_howto": {"intent": "deposit_howto", "route": "faq", "faq_query": "how to deposit"},
    "withdrawal_howto": {"intent": "withdrawal_howto", "route": "faq", "faq_query": "how to withdraw"},
    "forgot_password": {"intent": "forgot_password_howto", "route": "faq", "faq_query": "forgot password"},
    "global_human": {"intent": "explicit_human_request", "route": "human_handoff"},
}


def normalize_language(language: str | None) -> str:
    value = str(language or "").strip().lower()
    if value.startswith("zh"):
        return "zh"
    if value.startswith("en"):
        return "en"
    if value.startswith("es"):
        return "es"
    return "es"


def get_menu(menu_key: str, language: str | None = None) -> Menu:
    normalized_key = MENU_BY_NAV_BUTTON.get(str(menu_key or "").strip(), str(menu_key or "").strip())
    menus = MENUS.get(normalized_key)
    if not menus:
        raise KeyError(f"unknown livechat menu_key: {menu_key}")
    lang = normalize_language(language)
    menu = menus.get(lang) or menus.get("es") or next(iter(menus.values()))
    return {
        "menu_key": normalized_key,
        "title": str(menu.get("title") or ""),
        "buttons": [dict(button) for button in menu.get("buttons") or []],
        "language": lang if lang in menus else "es",
    }


def build_quick_replies_event(menu: Menu) -> dict[str, Any]:
    return {
        "type": "rich_message",
        "template_id": "quick_replies",
        "elements": [
            {
                "title": menu["title"],
                "buttons": [
                    {
                        "type": "message",
                        "text": button["label"],
                        "value": button["label"],
                        "postback_id": button["id"],
                        "user_ids": [],
                    }
                    for button in menu.get("buttons") or []
                ],
            }
        ],
    }


def fallback_text(menu: Menu) -> str:
    lines = [str(menu.get("title") or ""), ""]
    lines.extend(f"{index}. {button['label']}" for index, button in enumerate(menu.get("buttons") or [], start=1))
    return "\n".join(lines).strip()


def detect_button_id(text: str | None, menu_context: str | None = "main", language: str | None = None) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    normalized = _normalize_label(raw)
    alias = _detect_alias(normalized, menu_context)
    if alias:
        return alias
    menu_names = [menu_context] if menu_context else ["main", "deposit", "withdrawal", "other"]
    numeric = re.match(r"^(\d+)[\.\)\s]?$", raw)
    for menu_name in menu_names:
        try:
            menu = get_menu(str(menu_name), language)
        except KeyError:
            continue
        buttons = menu.get("buttons") or []
        if numeric:
            index = int(numeric.group(1)) - 1
            if 0 <= index < len(buttons):
                return buttons[index]["id"]
        for button in buttons:
            if _normalize_label(button["label"]) == normalized:
                return button["id"]
    return None


def _normalize_label(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    without_emoji_prefix = re.sub(r"^[^\w\u4e00-\u9fff]+", "", without_marks, flags=re.UNICODE)
    return without_emoji_prefix.strip().lower()


def _detect_alias(normalized: str, menu_context: str | None) -> str | None:
    ctx = menu_context or "main"
    if re.match(r"^(elegir otra opcion|otra opcion|cambiar opcion|choose another option|改選其他問題|改选其他问题)$", normalized):
        return "route_previous"
    if re.match(r"^(menu principal|main menu|主選單|主选单)$", normalized):
        return "route_main"
    if ctx in {"main", "deposit"}:
        if re.match(r"^(problemas?\s+de\s+deposito|problema\s+deposito|deposito|depositos?|recarga|recargas?|deposit issues?|deposit)$", normalized):
            return "deposit_menu"
        if re.match(r"^(deposito\s+no\s+acreditado|deposito\s+no\s+llego|recarga\s+no\s+llego|deposit not credited|存款未到帳|存款未到帐)$", normalized):
            return "main_deposito"
        if re.match(r"^(como\s+recargar|como\s+depositar|how\s+to\s+deposit|如何充值|充值教學|充值教学)$", normalized):
            return "deposit_howto"
    if ctx in {"main", "withdrawal"}:
        if re.match(r"^(problemas?\s+de\s+retiro|problema\s+retiro|retiro|retiros?|withdrawal issues?|withdrawal|提款問題|提款问题|提款)$", normalized):
            return "withdrawal_menu"
    if ctx in {"main", "other"}:
        if re.match(r"^(otros problemas|otro problema|otros|otro|other issues?|other problem|其他問題|其他问题)$", normalized):
            return "other_menu"
        if re.match(r"^(olvide mi contrasena|olvide contrasena|forgot password|忘記密碼|忘记密码)$", normalized):
            return "forgot_password"
    if re.match(r"^(atencion humana|humano|humana|agente|asesor|live support|真人客服)$", normalized):
        return "global_human"
    return None
