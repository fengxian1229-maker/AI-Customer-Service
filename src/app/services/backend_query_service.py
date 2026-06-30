import re
from typing import Any

from app.backends.factory import BackendProviderFactory, UnsupportedBackendProviderError
from app.backends.resolver import BackendConfigError, TenantBackendConfigResolver


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
            )
            provider = self.factory.create(config)
            query_result = provider.query_turnover_requirement(str(account_or_phone))
        except BackendConfigError as exc:
            return _failed(exc.code, str(exc))
        except UnsupportedBackendProviderError as exc:
            return _failed("FAILED_UNSUPPORTED_PROVIDER", str(exc))
        except Exception as exc:
            return _failed("FAILED_BACKEND_QUERY", _safe_error_message(exc))

        answer = build_withdrawal_blocked_answer(query_result)
        return {
            "status": "success",
            "answer": answer,
            "intent": intent,
            "account_or_phone": str(account_or_phone),
            "config_source": config.source,
            "query": sanitize_turnover_query_result(query_result),
        }


def build_withdrawal_blocked_answer(query_result: dict[str, Any]) -> str:
    if not query_result.get("player_found"):
        return "未查询到该用户名/手机号对应的玩家资料，请再次确认用户名或注册手机号是否正确。"
    active_count = int(query_result.get("active_requirements_count") or 0)
    remaining = query_result.get("remaining_turnover")
    if active_count > 0 or _positive_number(remaining):
        remaining_text = _format_number(remaining)
        return (
            f"后台查询显示当前可能仍有未完成流水要求，剩余流水约为 {remaining_text}。"
            "请先完成对应流水后再尝试提款。如你认为数据不正确，我们会继续人工复核。"
        )
    if query_result.get("is_met") is True:
        return "当前未查询到未完成流水要求。如仍无法提款，可能涉及其他风控或账户限制，我们会继续为你转人工/后台复核。"
    return "后台查询未返回明确流水结论，我们会继续人工复核。"


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
