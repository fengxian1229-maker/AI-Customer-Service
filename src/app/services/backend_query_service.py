import re
from typing import Any

from app.backends.factory import BackendProviderFactory, UnsupportedBackendProviderError
from app.backends.resolver import BackendConfigError, TenantBackendConfigResolver
from app.services.reply_intents import CustomerReplyIntent


class BackendQueryService:
    def __init__(self, resolver: TenantBackendConfigResolver, factory: BackendProviderFactory | None = None) -> None:
        self.resolver = resolver
        self.factory = factory or BackendProviderFactory()

    def execute(
        self,
        payload: dict[str, Any],
        tenant_id: str | None,
        channel_type: str | None = None,
        channel_instance_id: str | None = None,
    ) -> dict[str, Any]:
        intent = payload.get("intent")
        if intent != "withdrawal_blocked_or_rollover":
            return _failed("FAILED_UNSUPPORTED_INTENT", f"unsupported backend.query intent: {intent}")
        account_or_phone = payload.get("account_or_phone")
        if not account_or_phone:
            return _failed("FAILED_BUSINESS", "backend.query missing account_or_phone")

        try:
            config = self.resolver.resolve(
                tenant_id=tenant_id,
                channel_type=channel_type,
                channel_instance_id=channel_instance_id,
                livechat_group_id=payload.get("livechat_group_id"),
                platform=payload.get("platform"),
            )
            provider = self.factory.create(config)
            query_result = provider.query_turnover_requirement(str(account_or_phone))
        except BackendConfigError as exc:
            return _failed(exc.code, str(exc))
        except UnsupportedBackendProviderError as exc:
            return _failed("FAILED_UNSUPPORTED_PROVIDER", str(exc))
        except Exception as exc:
            return _failed("FAILED_BACKEND_QUERY", _safe_error_message(exc))

        reply_intent, reply_facts = build_withdrawal_blocked_reply(query_result)
        return {
            "status": "success",
            "reply_intent": str(reply_intent),
            "reply_facts": reply_facts,
            "intent": intent,
            "account_or_phone": str(account_or_phone),
            "livechat_group_id": config.livechat_group_id,
            "platform": config.platform,
            "merchant_code": config.merchant_code,
            "config_source": config.source,
            "query": sanitize_turnover_query_result(query_result),
        }


def build_withdrawal_blocked_reply(query_result: dict[str, Any]) -> tuple[CustomerReplyIntent, dict[str, Any]]:
    if not query_result.get("player_found"):
        return CustomerReplyIntent.BACKEND_PLAYER_NOT_FOUND, {}
    active_count = int(query_result.get("active_requirements_count") or 0)
    remaining = query_result.get("remaining_turnover")
    if active_count > 0 or _positive_number(remaining):
        return CustomerReplyIntent.BACKEND_TURNOVER_REMAINING, {"remaining_turnover": _format_number(remaining)}
    if query_result.get("is_met") is True:
        return CustomerReplyIntent.BACKEND_TURNOVER_MET, {}
    return CustomerReplyIntent.BACKEND_TURNOVER_UNKNOWN, {}


def sanitize_turnover_query_result(query_result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "player_found",
        "customer_id",
        "customer_name",
        "active_requirements_count",
        "remaining_turnover",
        "required_turnover",
        "valid_turnover",
        "is_met",
        "records_count",
        "query_windows",
    ]
    return {key: query_result.get(key) for key in keys if key in query_result}


def _failed(error_code: str, error_message: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "error_code": error_code,
        "error_message": error_message,
    }


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    patterns = [
        r"Authorization\s*[:=]\s*[^,\s}]+",
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        r"token\s*[:=]\s*[^,\s}]+",
        r"password\s*[:=]\s*[^,\s}]+",
        r"cookie\s*[:=]\s*[^,\s}]+",
        r"https?://[^\s,)]+",
    ]
    for pattern in patterns:
        replacement = "<redacted_backend_url>" if pattern.startswith("https?") else "<redacted>"
        message = re.sub(pattern, replacement, message, flags=re.I)
    return message[:500]


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _format_number(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number.is_integer():
        return str(int(number))
    return str(round(number, 2))
