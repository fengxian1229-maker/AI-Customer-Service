from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class CustomerReplyIntent(StrEnum):
    ASK_ACCOUNT_OR_PHONE = "ask_account_or_phone"
    BACKEND_QUERY_WAITING = "backend_query_waiting"
    BACKEND_PLAYER_NOT_FOUND = "backend_player_not_found"
    BACKEND_TURNOVER_REMAINING = "backend_turnover_remaining"
    BACKEND_TURNOVER_MET = "backend_turnover_met"
    BACKEND_TURNOVER_UNKNOWN = "backend_turnover_unknown"
    BACKEND_QUERY_FAILED = "backend_query_failed"
    WALLET_CHANGE_NEEDS_HUMAN_OR_CLARIFICATION = "wallet_change_needs_human_or_clarification"
    CLARIFICATION = "clarification"


class CustomerReply(TypedDict, total=False):
    intent: str
    facts: dict[str, Any]
    language: str | None
    text: str


def build_customer_reply(
    intent: CustomerReplyIntent | str,
    *,
    facts: dict[str, Any] | None = None,
    language: str | None = None,
    text: str | None = None,
) -> CustomerReply:
    reply: CustomerReply = {
        "intent": str(intent),
        "facts": dict(facts or {}),
        "language": language,
    }
    if text is not None:
        reply["text"] = text
    return reply
