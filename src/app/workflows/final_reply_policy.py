from __future__ import annotations

import re
from typing import Any

from app.services.chinese_script import adapt_chinese_script, chinese_script_mismatch
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

UNVERIFIED_TIME_COMMITMENT_PATTERN = re.compile(
    r"(24\s*小时|24\s*小時|几分钟|幾分鐘|马上|立即|今天内|今天內|tomorrow|within\s+\d+\s+hours|\bsoon\b)",
    re.I,
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
    "phone": {
        "zh-Hans": ("电话", "手机号", "注册手机号", "联系电话"),
        "zh-Hant": ("電話", "手機號", "註冊手機號", "聯絡電話"),
        "en": ("phone", "registered phone number", "contact number"),
        "es": ("teléfono", "telefono", "número registrado", "numero registrado"),
        "tl": ("phone", "registered number"),
        "th": ("เบอร์โทร", "เบอร์โทรที่ลงทะเบียน"),
        "my": ("phone", "registered phone"),
        "ms": ("telefon", "nombor telefon berdaftar"),
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
    "receipt_screenshot": {
        "zh-Hans": ("截图", "凭证截图", "付款截图", "存款付款截图", "提款截图"),
        "zh-Hant": ("截圖", "憑證截圖", "付款截圖"),
        "en": ("screenshot", "receipt screenshot", "payment proof", "proof"),
        "es": ("captura", "comprobante", "prueba de pago"),
        "tl": ("screenshot", "proof of payment"),
        "th": ("ภาพหน้าจอ", "หลักฐาน"),
        "my": ("screenshot", "proof"),
        "ms": ("tangkapan skrin", "bukti"),
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

INTERNAL_TELEGRAM_IDENTIFIER_PATTERN = re.compile(r"\b(?:tg|mock_tg):\d+\b|telegram_message_id|telegram_case_id", re.I)
INTERNAL_ORGANIZATION_LABEL_PATTERN = re.compile(
    r"(后台|後台|\bbackend\b|工作人员|人工后台|backend\s+staff|staff\s+reply|third[-\s]?party\s+platform|第三方平台|第三方\s*api|\bapi\b|接口)",
    re.I,
)
INTERNAL_BACKEND_TRANSFER_PATTERN = re.compile(
    r"(转交后台|轉交後台|转给后台|轉給後台|交给后台|交給後台|提交后台|提交給後台|同步给后台|同步至后台|补充给后台|補充給後台|"
    r"transferred?\s+to\s+backend|submitted?\s+to\s+backend|synced?\s+to\s+backend|hand(?:ed)?\s+off\s+to\s+backend|"
    r"call(?:ing)?\s+(?:a\s+)?third[-\s]?party\s+api|调用.{0,12}(?:api|接口|第三方平台))",
    re.I,
)
BACKEND_SYNC_CLAIM_PATTERN = re.compile(
    r"(已同步|同步至|同步给后台|已提交后台|提交给后台|已补充给后台|補充給後台|已補充給後台|sent to backend|synced to backend)",
    re.I,
)
STAFF_REPLY_CUSTOMER_FEEDBACK_PATTERN = re.compile(r"(收到|感谢|感謝).{0,8}(您|你|客户|客戶).{0,8}(反馈|反饋|回复|回覆)|your feedback|customer feedback", re.I)


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
        "used_facts": list(output.get("used_facts") or []),
        "reason": output.get("reason"),
    }


def accepted_with_warnings_result(
    output: dict[str, Any],
    *,
    violations: list[str] | None = None,
    warning_reason: str = "guardrail_audit",
) -> dict[str, Any]:
    result = accepted_result(output)
    result["status"] = "accepted_with_warnings"
    result["warning_reason"] = warning_reason
    if violations:
        result["violations"] = sorted(set(violations))
    return result


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
    if chinese_script_mismatch(text, reply_language):
        violations.append("language_script_mismatch")

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
        if phrase.lower() in lowered and not _is_verified_staff_backend_fact(str(phrase), plan):
            violations.append("forbidden_backend_fact")

    if INTERNAL_TELEGRAM_IDENTIFIER_PATTERN.search(text):
        violations.append("internal_telegram_identifier")

    if INTERNAL_ORGANIZATION_LABEL_PATTERN.search(text):
        violations.append("internal_organization_label")

    if INTERNAL_BACKEND_TRANSFER_PATTERN.search(text):
        violations.append("internal_backend_transfer_phrase")

    if UNVERIFIED_TIME_COMMITMENT_PATTERN.search(text):
        violations.append("unverified_time_commitment")

    if BACKEND_SYNC_CLAIM_PATTERN.search(text) and not _has_backend_sync_command(state, plan):
        violations.append("unverified_backend_sync_claim")

    if plan.get("kind") == "telegram_staff_reply" and STAFF_REPLY_CUSTOMER_FEEDBACK_PATTERN.search(text):
        violations.append("staff_reply_framed_as_customer_feedback")

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

    used_fact_violations = _validate_used_facts(state, output)
    violations.extend(used_fact_violations)

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


def _is_verified_staff_backend_fact(phrase: str, plan: dict[str, Any]) -> bool:
    if plan.get("kind") != "telegram_staff_reply":
        return False
    allowed_text = " ".join(str(fact or "").lower() for fact in plan.get("allowed_facts") or [])
    if not allowed_text:
        return False
    phrase_lower = phrase.lower()
    if phrase_lower in allowed_text:
        return True
    fact_groups = (
        ("到账", "credited", "successfully credited"),
        ("成功", "success", "successful"),
        ("完成", "处理完成", "completed", "done", "processed"),
        ("拒绝", "拒絕", "rejected"),
        ("失败", "失敗", "failed"),
        ("退款", "refunded"),
    )
    for group in fact_groups:
        if any(token in phrase_lower for token in group) and any(token in allowed_text for token in group):
            return True
    return False


def _validate_used_facts(state: dict[str, Any], output: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    used_facts = output.get("used_facts") or []
    if not isinstance(used_facts, list):
        return ["invalid_used_facts"]
    allowed_pool = _allowed_fact_pool(state)
    allowed_text = "\n".join(allowed_pool).lower()
    allow_unverified_low_risk_facts = _allows_low_risk_final_reply_facts(state)
    for raw_fact in used_facts:
        fact = str(raw_fact or "").strip()
        if not fact:
            continue
        fact_lower = fact.lower()
        if not allow_unverified_low_risk_facts and not any(fact_lower in allowed or allowed in fact_lower for allowed in allowed_pool if allowed):
            violations.append("unverified_used_fact")
        if UNVERIFIED_TIME_COMMITMENT_PATTERN.search(fact) and fact_lower not in allowed_text:
            violations.append("unverified_time_commitment")
        if BACKEND_SYNC_CLAIM_PATTERN.search(fact) and not _has_backend_sync_command(state, state.get("reply_plan") or {}):
            violations.append("unverified_backend_sync_claim")
        for phrase in UNVERIFIED_BACKEND_FACT_PHRASES:
            if phrase.lower() in fact_lower and not _is_verified_staff_backend_fact(str(phrase), state.get("reply_plan") or {}):
                violations.append("forbidden_backend_fact")
        for value in _critical_values(fact):
            if value.lower() not in allowed_text:
                violations.append("unverified_used_fact")
    return violations


def _allows_low_risk_final_reply_facts(state: dict[str, Any]) -> bool:
    plan = state.get("reply_plan") or {}
    intent = str((state.get("intent_result") or {}).get("intent") or "")
    return plan.get("kind") in {"casual_chat", "clarification", "emotion_care", "acknowledgement"} or intent in {
        "casual_chat",
        "clarification_needed",
        "abusive_or_emotional",
        "service_frustration",
        "acknowledgement",
    }


def _allowed_fact_pool(state: dict[str, Any]) -> list[str]:
    plan = state.get("reply_plan") or {}
    reply_language = _reply_language(state)
    values: list[str] = []
    values.extend(str(item) for item in plan.get("allowed_facts") or [] if item)
    values.append(str(plan.get("fallback_text") or ""))
    values.append(str(state.get("response_text_fallback") or ""))
    for key in ("node_facts", "rag_result", "backend_result"):
        values.extend(_flatten_fact_values(state.get(key)))
    adapted_values = []
    for value in values:
        if str(value).strip():
            adapted_values.append(value)
            adapted_values.append(adapt_chinese_script(value, reply_language))
    return [value.lower() for value in adapted_values if str(value).strip()]


def _flatten_fact_values(value: Any, prefix: str = "") -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return [text, f"{prefix}={text}"] if prefix else [text]
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_flatten_fact_values(item, next_prefix))
        return result
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_fact_values(item, prefix))
        return result
    return [str(value)]


def _critical_values(text: str) -> list[str]:
    return re.findall(r"\b\d+(?:\.\d+)?\b|[A-Za-z]{1,6}\d{4,}\b|\b\d{7,}\b", text)


def _has_backend_sync_command(state: dict[str, Any], plan: dict[str, Any]) -> bool:
    if plan.get("kind") in {"append_backend_case", "send_backend_case"}:
        return True
    for command in state.get("commands") or []:
        if str(command.get("type")) in {"telegram.append_to_case", "telegram.send_case_card"}:
            return True
    return False
