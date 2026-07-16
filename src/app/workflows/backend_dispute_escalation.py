import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any


AUTHORITATIVE_REPLY_INTENTS = {
    "backend_turnover_remaining",
    "backend_turnover_met",
}

DISPUTE_PATTERN = re.compile(
    r"(otra\s+vez|siempre|cuatro\s+veces|lo\s+devuelven|retiro\s+fallido|"
    r"sigue\s+fallando|mismo\s+problema|same\s+problem|still\s+failed|again|not\s+correct|"
    r"还是失败|還是失敗|仍然不行|還是不行|又失败|又失敗|不对|不對)",
    re.I,
)

DISPUTE_MEMORY_KEYS = (
    "backend_dispute_count",
    "backend_dispute_last_event_id",
    "backend_recheck_pending",
    "backend_recheck_origin_fingerprint",
    "backend_recheck_queued_dispute",
    "backend_recheck_queued_event_id",
)


def backend_conclusion_record(result_json: dict, *, recorded_at: Any = None) -> dict:
    canonical = {
        "intent": str(result_json.get("intent") or ""),
        "reply_intent": str(result_json.get("reply_intent") or ""),
        "reply_facts": (
            dict(result_json.get("reply_facts") or {})
            if isinstance(result_json.get("reply_facts"), dict)
            else {}
        ),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        **canonical,
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "recorded_at": _recorded_at_text(recorded_at),
    }


def apply_backend_conclusion(slot_memory: dict, result_json: dict, *, recorded_at: Any = None) -> dict:
    updated = dict(slot_memory or {})
    if not _is_authoritative_result(result_json):
        return updated
    record = backend_conclusion_record(result_json, recorded_at=recorded_at)
    previous = updated.get("backend_conclusion")
    same_fingerprint = isinstance(previous, dict) and previous.get("fingerprint") == record["fingerprint"]
    updated["backend_conclusion"] = record
    if same_fingerprint:
        updated["backend_dispute_count"] = int(updated.get("backend_dispute_count") or 0)
    else:
        updated["backend_dispute_count"] = 0
        updated.pop("backend_dispute_last_event_id", None)
    return updated


def evaluate_backend_dispute(state: dict) -> dict | None:
    memory = dict(state.get("slot_memory") or {})
    conclusion = memory.get("backend_conclusion")
    if not isinstance(conclusion, dict) or not conclusion.get("fingerprint"):
        return None
    if not _has_dispute_signal(state):
        return None

    event_id = state.get("event_id") or state.get("inbound_event_id")
    if memory.get("backend_recheck_pending"):
        queued_event_id = memory.get("backend_recheck_queued_event_id")
        if event_id is None or str(queued_event_id or "") != str(event_id):
            memory["backend_recheck_queued_dispute"] = True
            if event_id is not None:
                memory["backend_recheck_queued_event_id"] = event_id
        return {
            "state": {**state, "slot_memory": memory},
            "count": int(memory.get("backend_dispute_count") or 1),
            "should_handoff": False,
            "waiting_for_recheck": True,
        }

    last_event_id = memory.get("backend_dispute_last_event_id")
    if event_id is not None and str(last_event_id or "") == str(event_id):
        count = int(memory.get("backend_dispute_count") or 0)
    else:
        count = int(memory.get("backend_dispute_count") or 0) + 1
        memory["backend_dispute_count"] = count
        if event_id is not None:
            memory["backend_dispute_last_event_id"] = event_id

    return {
        "state": {**state, "slot_memory": memory},
        "count": count,
        "should_handoff": count >= 2,
        "waiting_for_recheck": False,
    }


def mark_backend_recheck_pending(slot_memory: dict) -> dict:
    updated = dict(slot_memory or {})
    conclusion = updated.get("backend_conclusion")
    fingerprint = conclusion.get("fingerprint") if isinstance(conclusion, dict) else None
    updated["backend_recheck_pending"] = True
    updated["backend_recheck_origin_fingerprint"] = fingerprint
    updated.pop("backend_recheck_queued_dispute", None)
    updated.pop("backend_recheck_queued_event_id", None)
    return updated


def resolve_backend_recheck(
    slot_memory: dict,
    result_json: dict,
    *,
    recorded_at: Any = None,
) -> dict:
    previous = dict(slot_memory or {})
    was_pending = bool(previous.get("backend_recheck_pending"))
    origin_fingerprint = previous.get("backend_recheck_origin_fingerprint")
    queued_dispute = bool(previous.get("backend_recheck_queued_dispute"))
    updated = apply_backend_conclusion(previous, result_json, recorded_at=recorded_at)
    conclusion = updated.get("backend_conclusion")
    current_fingerprint = conclusion.get("fingerprint") if isinstance(conclusion, dict) else None
    same_conclusion = bool(was_pending and origin_fingerprint and current_fingerprint == origin_fingerprint)

    for key in (
        "backend_recheck_pending",
        "backend_recheck_origin_fingerprint",
        "backend_recheck_queued_dispute",
        "backend_recheck_queued_event_id",
    ):
        updated.pop(key, None)

    should_handoff = bool(same_conclusion and queued_dispute)
    if should_handoff:
        updated["backend_dispute_count"] = max(int(updated.get("backend_dispute_count") or 0), 2)
    return {
        "slot_memory": updated,
        "same_conclusion": same_conclusion,
        "should_handoff": should_handoff,
    }


def clear_backend_dispute_memory(slot_memory: dict) -> dict:
    updated = dict(slot_memory or {})
    for key in DISPUTE_MEMORY_KEYS:
        updated.pop(key, None)
    return updated


def _is_authoritative_result(result_json: dict) -> bool:
    return (
        result_json.get("status") == "success"
        and str(result_json.get("reply_intent") or "") in AUTHORITATIVE_REPLY_INTENTS
        and bool(result_json.get("intent"))
    )


def _has_dispute_signal(state: dict) -> bool:
    intent_result = state.get("intent_result") if isinstance(state.get("intent_result"), dict) else {}
    if intent_result.get("emotion") == "frustrated" or intent_result.get("risk_level") == "elevated":
        return True
    text = str(state.get("raw_user_input") or state.get("rewritten_question") or "")
    return bool(DISPUTE_PATTERN.search(text))


def _recorded_at_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value:
        return str(value)
    return datetime.now(UTC).isoformat()
