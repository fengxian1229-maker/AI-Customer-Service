from __future__ import annotations

from typing import Any

from app.services.language_policy import detect_language_deterministic, normalize_language_code

UNVERIFIED_BACKEND_FACT_PHRASES = (
    "已到账",
    "到账成功",
    "已成功",
    "成功到账",
    "已完成",
    "处理完成",
    "已拒绝",
    "已失败",
    "已退款",
    "退款完成",
    "credited",
    "successfully credited",
    "completed",
    "rejected",
    "refunded",
)

SEMANTIC_ALIASES = {
    "account_or_phone": {
        "zh-Hans": ("用户名", "注册手机号", "账号", "手机号"),
        "zh-Hant": ("用戶名", "註冊手機號", "帳號", "手機號"),
        "en": ("username", "registered phone number", "account", "phone"),
        "es": ("usuario", "teléfono registrado", "telefono registrado", "cuenta"),
        "tl": ("username", "rehistradong numero", "account"),
        "th": ("ชื่อผู้ใช้", "เบอร์โทรที่ลงทะเบียน", "บัญชี"),
        "my": ("username", "registered phone", "account"),
        "ms": ("nama pengguna", "nombor telefon berdaftar", "akaun"),
    },
    "deposit_screenshot": {
        "zh-Hans": ("存款付款截图", "付款截图", "充值截图"),
        "zh-Hant": ("存款付款截圖", "付款截圖", "儲值截圖"),
        "en": ("deposit screenshot", "payment screenshot", "proof of payment", "payment proof"),
        "es": ("captura de depósito", "captura de deposito", "comprobante de pago"),
        "tl": ("screenshot ng deposito", "proof of payment"),
        "th": ("ภาพหน้าจอการฝากเงิน", "หลักฐานการชำระเงิน"),
        "my": ("deposit screenshot", "payment proof"),
        "ms": ("tangkapan skrin deposit", "bukti pembayaran"),
    },
    "withdrawal_screenshot": {
        "zh-Hans": ("提款截图", "提款申请截图"),
        "zh-Hant": ("提款截圖", "提款申請截圖"),
        "en": ("withdrawal screenshot", "withdrawal request screenshot"),
        "es": ("captura de retiro", "solicitud de retiro"),
        "tl": ("screenshot ng withdrawal", "withdrawal request screenshot"),
        "th": ("ภาพหน้าจอการถอนเงิน", "คำขอถอนเงิน"),
        "my": ("withdrawal screenshot",),
        "ms": ("tangkapan skrin pengeluaran",),
    },
    "backend_waiting_notice": {
        "zh-Hans": ("请稍等", "后台", "查询", "确认"),
        "zh-Hant": ("請稍等", "後台", "查詢", "確認"),
        "en": ("please wait", "checking", "confirm", "backend"),
        "es": ("espera", "verificando", "confirmar"),
        "tl": ("maghintay", "chine-check", "kumpirma"),
        "th": ("รอสักครู่", "ตรวจสอบ", "ยืนยัน"),
        "my": ("please wait", "checking"),
        "ms": ("sila tunggu", "semak", "sahkan"),
    },
    "human_handoff_notice": {
        "zh-Hans": ("转接真人客服", "真人客服"),
        "zh-Hant": ("轉接真人客服", "真人客服"),
        "en": ("human agent", "live agent", "support agent"),
        "es": ("agente humano", "soporte"),
        "tl": ("human agent", "support agent"),
        "th": ("เจ้าหน้าที่", "พนักงาน"),
        "my": ("human agent", "support"),
        "ms": ("ejen manusia", "sokongan"),
    },
    "pending_reply_identity": {
        "zh-Hans": ("用户名", "注册手机号", "邮箱", "识别资料"),
        "zh-Hant": ("用戶名", "註冊手機號", "信箱", "識別資料"),
        "en": ("username", "registered phone number", "email", "identity"),
        "es": ("usuario", "teléfono registrado", "correo", "identificación"),
        "tl": ("username", "rehistradong numero", "email"),
        "th": ("ชื่อผู้ใช้", "เบอร์โทรที่ลงทะเบียน", "อีเมล"),
        "my": ("username", "registered phone", "email"),
        "ms": ("nama pengguna", "nombor telefon berdaftar", "emel"),
    },
}

BACKEND_WAITING_FORBIDDEN_PROMISES = (
    "一定",
    "马上到账",
    "立即到账",
    "保证",
    "已经处理",
    "已处理",
    "will be credited",
    "guarantee",
)

HUMAN_HANDOFF_FORBIDDEN_PROMISES = (
    "已接入",
    "已经接入",
    "真人已接入",
    "马上处理",
    "立即处理",
    "agent is connected",
    "agent has joined",
)


def build_reply_plan(
    *,
    kind: str,
    fallback_text: str,
    must_say: list[str] | None = None,
    must_say_exact: list[str] | None = None,
    semantic_required_items: list[str] | None = None,
    must_not_say: list[str] | None = None,
    missing_slots: list[str] | None = None,
    allowed_facts: list[str] | None = None,
    staff_reply_key_facts: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "fallback_text": fallback_text,
        "must_say": list(must_say or []),
        "must_say_exact": list(must_say_exact or []),
        "semantic_required_items": list(semantic_required_items or []),
        "must_not_say": list(must_not_say or []),
        "missing_slots": list(missing_slots or []),
        "allowed_facts": list(allowed_facts or []),
        "staff_reply_key_facts": list(staff_reply_key_facts or []),
        "metadata": dict(metadata or {}),
    }


def fallback_result(reason: str, *, violations: list[str] | None = None, error: Exception | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "fallback",
        "fallback_reason": reason,
    }
    if violations:
        result["violations"] = sorted(set(violations))
    if error:
        result["error_type"] = type(error).__name__
        result["error_message"] = str(error)[:1000]
    return result


def accepted_result(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "accepted",
        "language": output.get("language"),
        "tone": output.get("tone"),
        "confidence": float(output.get("confidence") or 0.0),
        "safety_flags": list(output.get("safety_flags") or []),
        "reason": output.get("reason"),
    }


def validate_final_reply_output(state: dict[str, Any], output: dict[str, Any]) -> list[str]:
    text = str(output.get("text") or "").strip()
    plan = state.get("reply_plan") or {}
    violations: list[str] = []
    if not text:
        violations.append("empty_text")
        return violations

    lowered = text.lower()
    reply_language = _reply_language(state)
    output_language = normalize_language_code(output.get("language"))
    if reply_language != "unknown" and output_language != reply_language:
        violations.append("language_mismatch")

    fallback_language = detect_language_deterministic(plan.get("fallback_text") or state.get("response_text_fallback")).get("detected_language")
    should_enforce_legacy_must_say = reply_language in {"unknown", fallback_language}

    for phrase in plan.get("must_say_exact") or []:
        if phrase and str(phrase).lower() not in lowered:
            violations.append("missing_exact_phrase")
    if should_enforce_legacy_must_say:
        for phrase in plan.get("must_say") or []:
            if phrase and str(phrase).lower() not in lowered:
                violations.append("missing_required_phrase")

    for item in plan.get("semantic_required_items") or []:
        if not _contains_semantic_item(lowered, str(item), reply_language):
            violations.append(f"missing_semantic_{item}")
    for phrase in plan.get("must_not_say") or []:
        if phrase and str(phrase).lower() in lowered:
            violations.append("forbidden_phrase")

    for phrase in UNVERIFIED_BACKEND_FACT_PHRASES:
        if phrase.lower() in lowered:
            violations.append("forbidden_backend_fact")

    if plan.get("kind") == "ask_missing_slots":
        for slot in plan.get("missing_slots") or state.get("missing_slots") or []:
            if not _contains_semantic_item(lowered, str(slot), reply_language):
                violations.append(f"missing_slot_{slot}")

    if state.get("workflow_stage") in {"waiting_backend", "backend_querying"} or plan.get("kind") in {
        "backend_waiting",
        "send_backend_case",
        "append_backend_case",
    }:
        if any(phrase.lower() in lowered for phrase in BACKEND_WAITING_FORBIDDEN_PROMISES):
            violations.append("backend_waiting_promise")

    if plan.get("kind") == "human_handoff":
        if any(phrase.lower() in lowered for phrase in HUMAN_HANDOFF_FORBIDDEN_PROMISES):
            violations.append("human_handoff_promise")

    for fact in plan.get("staff_reply_key_facts") or []:
        if fact and str(fact).lower() not in lowered:
            violations.append("staff_reply_key_fact_missing")

    return sorted(set(violations))


def _reply_language(state: dict[str, Any]) -> str:
    language = normalize_language_code(state.get("reply_language"))
    if language != "unknown":
        return language
    tenant = state.get("tenant_persona") or {}
    return normalize_language_code(tenant.get("default_language"))


def _contains_semantic_item(lowered_text: str, item: str, reply_language: str) -> bool:
    aliases_by_language = SEMANTIC_ALIASES.get(item)
    if not aliases_by_language:
        return item.lower() in lowered_text
    languages = [reply_language]
    if reply_language not in {"en", "unknown"}:
        languages.append("en")
    languages.extend(["zh-Hans", "zh-Hant"])
    for language in languages:
        for alias in aliases_by_language.get(language, ()):
            if alias and alias.lower() in lowered_text:
                return True
    return False
