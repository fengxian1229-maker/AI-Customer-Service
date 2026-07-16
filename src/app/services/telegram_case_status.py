import re
from typing import Any


MONEY_INTENTS = {"deposit_missing", "withdrawal_missing"}

_ASK_CUSTOMER_PATTERN = re.compile(
    r"\b(?:please\s+(?:provide|send|share|confirm)|need\s+(?:the|your)|could\s+you\s+(?:provide|send|share))\b|"
    r"(?:请|請).*(?:提供|发送|發送|确认|確認)",
    re.IGNORECASE,
)
_WAIT_PATTERN = re.compile(
    r"\b(?:still\s+checking|under\s+review|in\s+review|processing|pending|please\s+wait)\b|"
    r"(?:审核中|審核中|处理中|處理中|查询中|查詢中|请等待|請等待)",
    re.IGNORECASE,
)
_TERMINAL_OTHER_PATTERN = re.compile(
    r"\b(?:cancelled|canceled|rejected|invalid|voided)\b|(?:已取消|被拒绝|被拒絕|无效|無效)",
    re.IGNORECASE,
)
_SUCCESS_PATTERN = re.compile(
    r"\b(?:completed|complete|successful|successfully|credited|received|paid)\b|"
    r"(?:已完成|完成成功|已成功|已(?:经|經)?到(?:账|賬)|入账成功|入賬成功)",
    re.IGNORECASE,
)
_DEPOSIT_PATTERN = re.compile(r"\bdeposit\b|(?:存款|充值|入金)", re.IGNORECASE)
_WITHDRAWAL_PATTERN = re.compile(r"\bwithdraw(?:al)?\b|\bcash\s*out\b|(?:提款|取款|提现|提現|出金)", re.IGNORECASE)

_STICKY_STATUSES = {"completion_disputed", "completed_confirmed_by_customer"}


def classify_money_case_status(
    intent: str,
    raw_reply: str,
    current_status: str | None = None,
) -> str:
    text = str(raw_reply or "").strip()
    if intent not in MONEY_INTENTS:
        return current_status or "under_review"
    if current_status in _STICKY_STATUSES:
        return str(current_status)
    if _ASK_CUSTOMER_PATTERN.search(text):
        return "waiting_customer"
    if _WAIT_PATTERN.search(text):
        return "under_review"
    if _matching_completion(intent, text):
        return "completed_by_staff"
    if _TERMINAL_OTHER_PATTERN.search(text):
        return "terminal_other"
    return "under_review"


def normalize_legacy_case_status(case: dict[str, Any]) -> str:
    status = str(case.get("status") or "").strip()
    if status and status != "created":
        return status
    slot_memory = case.get("slot_memory") or {}
    if str(slot_memory.get("last_telegram_staff_reply_type") or "") == "long_wait":
        return "under_review"
    if slot_memory.get("telegram_case_resolved_at"):
        resolution = str(slot_memory.get("telegram_case_resolution_text") or "")
        classified = classify_money_case_status(str(case.get("intent") or ""), resolution)
        if classified == "completed_by_staff":
            return classified
        return "under_review"
    return "awaiting_review"


def _matching_completion(intent: str, text: str) -> bool:
    if not _SUCCESS_PATTERN.search(text):
        return False
    if re.search(
        r"\b(?:not|never|hasn't|haven't|wasn't|isn't)\b.{0,24}\b(?:completed|complete|successful|credited|received|paid)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    has_deposit = bool(_DEPOSIT_PATTERN.search(text))
    has_withdrawal = bool(_WITHDRAWAL_PATTERN.search(text))
    if intent == "deposit_missing":
        deposit_result = has_deposit or bool(re.search(r"\bcredited\b|(?:已(?:经|經)?到(?:账|賬)|入账|入賬)", text, re.IGNORECASE))
        return deposit_result and not has_withdrawal
    withdrawal_result = has_withdrawal or bool(re.search(r"\breceived\b|(?:已(?:经|經)?到(?:账|賬))", text, re.IGNORECASE))
    return withdrawal_result and not has_deposit
