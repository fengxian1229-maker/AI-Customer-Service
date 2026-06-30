from __future__ import annotations

from typing import Any

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

MISSING_SLOT_REQUIRED_PHRASES = {
    "account_or_phone": ("用户名", "注册手机号", "账号", "手机号", "account", "phone"),
    "deposit_screenshot": ("存款付款截图", "付款截图", "充值截图", "screenshot", "proof"),
    "withdrawal_screenshot": ("提款截图", "提款申请截图", "withdrawal screenshot", "screenshot"),
    "amount": ("金额", "amount"),
    "order_id": ("订单", "单号", "order"),
    "payment_channel": ("通道", "渠道", "channel"),
    "pending_reply_identity": ("用户名", "注册手机号", "邮箱", "识别资料", "account", "phone", "email"),
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
    for phrase in plan.get("must_say") or []:
        if phrase and str(phrase).lower() not in lowered:
            violations.append("missing_required_phrase")
    for phrase in plan.get("must_not_say") or []:
        if phrase and str(phrase).lower() in lowered:
            violations.append("forbidden_phrase")

    for phrase in UNVERIFIED_BACKEND_FACT_PHRASES:
        if phrase.lower() in lowered:
            violations.append("forbidden_backend_fact")

    if plan.get("kind") == "ask_missing_slots":
        for slot in plan.get("missing_slots") or state.get("missing_slots") or []:
            aliases = MISSING_SLOT_REQUIRED_PHRASES.get(str(slot), (str(slot),))
            if not any(alias.lower() in lowered for alias in aliases):
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
