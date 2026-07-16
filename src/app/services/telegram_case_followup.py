import re
from datetime import UTC, datetime
from typing import Any

from app.services.telegram_case_status import normalize_legacy_case_status


_FOLLOWUP_PATTERN = re.compile(
    r"\b(?:still\s+(?:not|haven't|hasn't)|not\s+(?:received|credited)|where\s+is|how\s+much\s+longer|"
    r"no\s+ha\s+llegado|a[uú]n\s+no|todav[ií]a\s+no)\b|"
    r"(?:怎么还没|怎麼還沒|还没收到|還沒收到|仍未收到|没有到账|沒有到賬|未到账|未到賬|ยังไม่ได้|belum\s+masuk)",
    re.IGNORECASE,
)
_CUSTOMER_CONFIRMED_PATTERN = re.compile(
    r"\b(?:has\s+arrived|have\s+received|received\s+it|credited\s+now|got\s+it)\b|"
    r"(?:已经收到|已經收到|收到了|已经到账|已經到賬|到账了|到賬了)",
    re.IGNORECASE,
)
_TRANSACTION_KEYS = (
    "order_id",
    "order_no",
    "transaction_id",
    "transaction_ref",
    "reference",
    "withdrawal_id",
    "deposit_id",
)

CUSTOMER_UPDATE_FALLBACK = {
    "deposit_missing": "The customer reports that the deposit has still not been credited.",
    "withdrawal_missing": "The customer reports that the withdrawal has still not been received.",
}
CASE_TYPE = {
    "deposit_missing": "Deposit Not Credited",
    "withdrawal_missing": "Withdrawal Not Received",
}
ACTION_REQUIRED = {
    "deposit_missing": "Please recheck whether the deposit has been credited and reply with the latest status.",
    "withdrawal_missing": "Please recheck whether the withdrawal has been completed and reply with the latest status.",
}
PREVIOUS_STATUS = {
    "awaiting_review": "Awaiting Review",
    "under_review": "Under Review",
    "completion_disputed": "Completion Disputed",
}


def resolve_money_case_followup(
    candidates: list[dict],
    text: str,
    inherited_root_message_id: int | None,
) -> dict:
    is_followup = is_money_case_followup_text(text)
    is_confirmation = bool(_CUSTOMER_CONFIRMED_PATTERN.search(str(text or ""))) and not is_followup
    if not is_followup and not is_confirmation:
        return {"status": "none"}

    eligible = []
    for raw in candidates or []:
        case = dict(raw)
        case["status"] = normalize_legacy_case_status(case)
        if case["status"] in {"completed_confirmed_by_customer", "terminal_other"}:
            continue
        if not is_confirmation and case["status"] == "waiting_customer":
            continue
        if case["status"] == "completed_by_staff" and not (is_followup or is_confirmation):
            continue
        eligible.append(case)
    if not eligible:
        return {"status": "none"}

    normalized_text = str(text or "")
    transaction_matches = [
        case for case in eligible if any(_contains_exact_identifier(normalized_text, value) for value in _transaction_values(case))
    ]
    if len(transaction_matches) == 1:
        return _matched(transaction_matches[0], customer_confirmed=is_confirmation)
    if len(transaction_matches) > 1:
        return {"status": "ambiguous"}

    if inherited_root_message_id is not None:
        root_matches = [
            case for case in eligible if str(case.get("root_message_id")) == str(inherited_root_message_id)
        ]
        if len(root_matches) == 1:
            return _matched(root_matches[0], customer_confirmed=is_confirmation)
    if len(eligible) == 1:
        return _matched(eligible[0], customer_confirmed=is_confirmation)
    return {"status": "ambiguous"}


def build_money_case_followup_dedup_key(case: dict, source_thread_id: str) -> str:
    return (
        f"telegram.case.followup:{case['telegram_chat_id']}:"
        f"{case['root_message_id']}:{source_thread_id}"
    )


def summarize_customer_update(source_text: str, intent: str, translator=None) -> str:
    fallback = CUSTOMER_UPDATE_FALLBACK[intent]
    if translator is None:
        return fallback
    try:
        candidate = str(translator.translate_followup(source_text, intent) or "").strip()
    except Exception:
        return fallback
    return candidate if validate_customer_update(source_text, candidate) else fallback


def validate_customer_update(source_text: str, candidate: str) -> bool:
    if not candidate or len(candidate) > 300:
        return False
    if re.search(r"[^\x00-\x7f]", candidate) or not re.search(r"[A-Za-z]", candidate):
        return False
    candidate_folded = candidate.casefold()
    english_markers = re.findall(
        r"\b(?:the|customer|reports?|deposit|withdrawal|order|transaction|amount|has|have|is|still|not|received|credited|day|days|hour|hours)\b",
        candidate_folded,
    )
    if len(english_markers) < 2:
        return False
    source = str(source_text or "")
    for transaction_id in re.findall(r"\b[A-Za-z]{1,12}[-_]?\d{3,}\b", source):
        if transaction_id.casefold() not in candidate_folded:
            return False
    source_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", source)
    candidate_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", candidate))
    if any(number not in candidate_numbers for number in source_numbers):
        return False
    source_durations = _canonical_durations(source)
    candidate_durations = _canonical_durations(candidate)
    if not source_durations.issubset(candidate_durations):
        return False
    positive_claim = re.search(
        r"\b(?:has|was|is)\s+(?:been\s+)?(?:completed|credited|received|paid)\b|\bsuccessfully\b",
        candidate,
        re.IGNORECASE,
    )
    source_success = re.search(
        r"\b(?:completed|credited|received|paid|successful|successfully)\b|(?:已完成|已成功|已到账|已到賬)",
        source,
        re.IGNORECASE,
    )
    source_negated_success = re.search(
        r"\b(?:not|never|still\s+not|hasn't|haven't|wasn't|isn't)\b.{0,24}\b(?:completed|credited|received|paid)\b",
        source,
        re.IGNORECASE,
    )
    if positive_claim and (not source_success or source_negated_success):
        return False
    unsupported_claims = re.compile(
        r"\b(?:under\s+review|approved|rejected|because|due\s+to|caused\s+by|will\s+arrive|within\s+\d+|eta)\b",
        re.IGNORECASE,
    )
    if unsupported_claims.search(candidate) and not unsupported_claims.search(source):
        return False
    return True


def is_money_case_followup_text(text: str) -> bool:
    return bool(_FOLLOWUP_PATTERN.search(str(text or "")))


def build_telegram_case_followup(
    command: dict,
    case: dict,
    followup: dict,
    customer_update_en: str,
) -> dict:
    payload = command.get("payload_json") or command.get("payload") or {}
    intent = str(case.get("intent") or payload.get("intent") or "withdrawal_missing")
    kind = str(followup.get("follow_up_kind") or payload.get("follow_up_kind") or "pending_follow_up")
    if kind == "completion_dispute":
        text = _completion_dispute_text(intent)
    else:
        follow_up_number = int(followup.get("follow_up_number") or 2)
        previous_status = PREVIOUS_STATUS.get(
            str(followup.get("previous_status") or payload.get("previous_status") or "under_review"),
            "Under Review",
        )
        current_thread_id = command.get("thread_id") or payload.get("thread_id") or "(not provided)"
        timestamp = datetime.now(UTC).isoformat()
        text = "\n".join(
            [
                "🔔 FOLLOW-UP REQUIRED",
                "",
                "The customer has contacted us again in a new chat thread regarding the same case.",
                "",
                f"Case Type: {CASE_TYPE[intent]}",
                f"Follow-up: #{follow_up_number}",
                f"Previous Status: {previous_status}",
                f"Customer Update: {customer_update_en}",
                "",
                "Action Required:",
                ACTION_REQUIRED[intent],
                "",
                f"New Thread ID: {current_thread_id}",
                f"Follow-up Time: {timestamp}",
                "",
                "Please reply directly to this message with the latest case status.",
            ]
        )
    urls = list(
        dict.fromkeys(
            str(url)
            for url in ((payload.get("supplement") or {}).get("attachment_urls") or [])
            if url
        )
    )
    return {
        "chat_id": str(case["telegram_chat_id"]),
        "thread_id": case.get("telegram_message_thread_id"),
        "root_message_id": int(case["root_message_id"]),
        "text": text,
        "attachments": [{"url": url, "name": "supplement"} for url in urls],
    }


def _matched(case: dict, *, customer_confirmed: bool = False) -> dict:
    if customer_confirmed:
        kind = "customer_confirmed_resolved"
    else:
        kind = "completion_dispute" if case.get("status") == "completed_by_staff" else "pending_follow_up"
    return {"status": "matched", "case": case, "follow_up_kind": kind}


def _transaction_values(case: dict[str, Any]) -> list[str]:
    slot_memory = case.get("slot_memory") or {}
    values = []
    for source in (case, slot_memory):
        for key in _TRANSACTION_KEYS:
            value = str(source.get(key) or "").strip()
            if value:
                values.append(value)
    return values


def _contains_exact_identifier(text: str, value: str) -> bool:
    return bool(re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", text, re.IGNORECASE))


def _canonical_durations(text: str) -> set[tuple[int, str]]:
    result: set[tuple[int, str]] = set()
    word_numbers = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "uno": 1,
        "una": 1,
        "dos": 2,
        "tres": 3,
    }
    for raw_number, raw_unit in re.findall(
        r"\b(\d+|one|two|three|four|five|uno|una|dos|tres)\s*(days?|hours?|d[ií]as?|horas?)\b",
        text,
        re.IGNORECASE,
    ):
        number = int(raw_number) if raw_number.isdigit() else word_numbers[raw_number.casefold()]
        unit = "day" if raw_unit.casefold().startswith(("day", "dí", "di")) else "hour"
        result.add((number, unit))
    chinese_numbers = {"一": 1, "两": 2, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5}
    for raw_number, raw_unit in re.findall(r"(\d+|[一两兩二三四五])\s*(天|日|小时|小時)", text):
        number = int(raw_number) if raw_number.isdigit() else chinese_numbers[raw_number]
        result.add((number, "day" if raw_unit in {"天", "日"} else "hour"))
    return result


def _completion_dispute_text(intent: str) -> str:
    if intent == "deposit_missing":
        return "\n".join(
            [
                "⚠️ CREDITING RESULT DISPUTED",
                "",
                "The customer reports that the deposit is still not credited, although the previous case update marked it as completed.",
                "",
                "Action Required:",
                "Please verify the final transaction status and confirm the crediting reference or completion evidence.",
                "",
                "Please reply directly to this message with the verification result.",
            ]
        )
    return "\n".join(
        [
            "⚠️ COMPLETION DISPUTED",
            "",
            "The customer reports that the withdrawal is still not received, although the previous case update marked it as completed.",
            "",
            "Action Required:",
            "Please verify the final transaction status and confirm the payment reference or completion evidence.",
            "",
            "Please reply directly to this message with the verification result.",
        ]
    )
