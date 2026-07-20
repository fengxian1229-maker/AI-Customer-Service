from typing import Literal


IntentHandlerKind = Literal["knowledge", "workflow", "handoff", "direct_reply", "clarification"]

INTENT_HANDLERS: dict[str, IntentHandlerKind] = {
    "deposit_howto": "knowledge",
    "withdrawal_howto": "knowledge",
    "forgot_password_howto": "knowledge",
    "screenshot_upload_howto": "knowledge",
    "turnover_requirement_query": "workflow",
    "explicit_human_request": "handoff",
    "account_access_issue": "handoff",
    "account_profile_or_wallet_change": "handoff",
    "screenshot_upload_failed": "handoff",
    "wallet_identity_risk": "handoff",
    "account_verification_issue": "handoff",
    "game_technical_issue": "handoff",
    "abuse_or_fraud_risk": "handoff",
    "unsupported_concrete_issue": "handoff",
    "casual_chat": "direct_reply",
    "service_frustration": "direct_reply",
    "abusive_or_emotional": "direct_reply",
    "clarification_needed": "clarification",
    "backend_fact_like": "clarification",
}
