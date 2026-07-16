from __future__ import annotations

from typing import Any

from app.services.language_policy import normalize_language_code


TERMINAL_AI_FAILURE_REASONS = frozenset({"timeout", "exception", "provider_failure"})
AI_FAILURE_NOTICE_FALLBACK_LANGUAGE = "es"
AI_FAILURE_HANDOFF_NOTICES = {
    "zh-Hans": "很抱歉，AI客服当前出现临时故障。为了不影响您继续处理问题，现为您转接人工客服继续协助。",
    "zh-Hant": "很抱歉，AI客服目前出現暫時故障。為了不影響您繼續處理問題，現為您轉接真人客服繼續協助。",
    "en": "Sorry, the automated assistant is having a temporary technical issue. To avoid delaying your case, I will transfer you to a human agent for continued help.",
    "es": "Lo sentimos, el asistente automático tuvo un inconveniente técnico temporal. Para no afectar la atención de su caso, le transferiremos con un agente humano para que continúe ayudándole.",
    "tl": "Paumanhin, pansamantalang nagkaroon ng teknikal na problema ang automated assistant. Upang hindi maantala ang iyong concern, ililipat ka namin sa isang human agent para patuloy kang matulungan.",
    "th": "ขออภัย ผู้ช่วยอัตโนมัติเกิดปัญหาทางเทคนิคชั่วคราว เพื่อไม่ให้การดำเนินการของคุณล่าช้า เราจะโอนคุณไปยังเจ้าหน้าที่เพื่อช่วยเหลือต่อ",
    "my": "တောင်းပန်ပါတယ်၊ အလိုအလျောက်အကူစနစ်တွင် ယာယီနည်းပညာပြဿနာ ဖြစ်ပေါ်နေပါသည်။ သင့်ကိစ္စ မနှောင့်နှေးစေရန် လူသားဝန်ထမ်းထံ လွှဲပြောင်းပေးပါမည်။",
    "ms": "Maaf, pembantu automatik sedang mengalami masalah teknikal sementara. Untuk mengelakkan urusan anda tertangguh, kami akan memindahkan anda kepada ejen manusia untuk bantuan lanjut.",
}

FAQ_TRANSLATION_FAILURE_NOTICES = {
    "zh-Hans": "抱歉，暂时无法生成这项说明。请联系人工客服继续处理。",
    "zh-Hant": "抱歉，暫時無法產生這項說明。請聯絡真人客服繼續處理。",
    "en": "Sorry, I cannot provide this guide in your language right now. Please contact a human agent for further help.",
    "es": "Lo siento, temporalmente no puedo ofrecer esta guía en su idioma. Solicite ayuda a un agente humano para continuar.",
    "tl": "Paumanhin, hindi ko maibibigay ang gabay na ito sa iyong wika sa ngayon. Mangyaring humingi ng tulong sa isang human agent.",
    "th": "ขออภัย ขณะนี้ไม่สามารถแสดงคำแนะนำนี้เป็นภาษาของคุณได้ โปรดติดต่อเจ้าหน้าที่เพื่อขอความช่วยเหลือต่อ",
    "my": "တောင်းပန်ပါသည်၊ ယခုလောလောဆယ် ဤလမ်းညွှန်ကို သင့်ဘာသာစကားဖြင့် မပေးနိုင်သေးပါ။ ဆက်လက်အကူအညီရယူရန် လူသားဝန်ထမ်းကို ဆက်သွယ်ပါ။",
    "ms": "Maaf, panduan ini tidak dapat diberikan dalam bahasa anda buat masa ini. Sila hubungi ejen manusia untuk bantuan lanjut.",
}


def ai_failure_handoff_notice(language: str | None) -> str:
    normalized = normalize_language_code(language)
    return AI_FAILURE_HANDOFF_NOTICES.get(
        normalized,
        AI_FAILURE_HANDOFF_NOTICES[AI_FAILURE_NOTICE_FALLBACK_LANGUAGE],
    )


def faq_translation_failure_notice(language: str | None) -> str:
    normalized = normalize_language_code(language)
    return FAQ_TRANSLATION_FAILURE_NOTICES.get(
        normalized,
        FAQ_TRANSLATION_FAILURE_NOTICES[AI_FAILURE_NOTICE_FALLBACK_LANGUAGE],
    )


def terminal_ai_failure_reason(final_reply_result: dict[str, Any] | None) -> str | None:
    result = final_reply_result or {}
    if result.get("status") != "fallback":
        return None
    reason = str(result.get("fallback_reason") or "")
    return reason if reason in TERMINAL_AI_FAILURE_REASONS else None
