import argparse
import json
import os
import re
from datetime import datetime, timedelta

from app.backends.resolver import BackendConfigError
from app.backends.resolver import TenantBackendConfigResolver
from app.core.settings import Settings
from app.services.backend_query_service import sanitize_turnover_query_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only TAC backend probe.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("preflight", help="Check TAC backend configuration and login readiness.")

    player = subparsers.add_parser("player", help="Query TAC player profile.")
    player.add_argument("username")
    player.add_argument("merchant_code", nargs="?")

    turnover = subparsers.add_parser("turnover", help="Query TAC turnover requirement.")
    turnover.add_argument("username")
    turnover.add_argument("merchant_code", nargs="?")

    deposit = subparsers.add_parser("deposit", help="Query TAC deposit records.")
    deposit.add_argument("username")
    deposit.add_argument("date_from")
    deposit.add_argument("date_to")
    deposit.add_argument("merchant_code", nargs="?")

    contribution = subparsers.add_parser("contribution", help="Query TAC player contribution report.")
    contribution.add_argument("username")
    contribution.add_argument("date_from")
    contribution.add_argument("date_to")
    contribution.add_argument("merchant_code", nargs="?")
    return parser


def run_preflight(settings: Settings, factory=None) -> dict:
    provider_type = (settings.backend_provider_type or "").strip()
    has_login_operator = bool((settings.backend_login_operator or "").strip())
    has_login_password = bool((settings.backend_login_password or "").strip())
    has_totp_secret = bool((settings.backend_totp_secret or "").strip())
    has_login_merchant = bool((settings.backend_login_merchant or "").strip())
    result = {
        "worker": "tac_backend_preflight",
        "backend_query_enabled": bool(settings.backend_query_enabled),
        "provider_type": provider_type,
        "config_source": "env_default",
        "has_base_url": bool((settings.backend_base_url or "").strip()),
        "has_merchant_code": bool((settings.backend_merchant_code or "").strip()),
        "has_authorization": bool((settings.backend_authorization or "").strip()),
        "has_login_operator": has_login_operator,
        "has_login_password": has_login_password,
        "has_totp_secret": has_totp_secret,
        "has_login_merchant": has_login_merchant,
        "preflight_status": "DISABLED",
        "safe_to_probe": False,
        "login_attempted": False,
        "login_success": False,
        "settings_warning": _settings_warnings(),
    }
    if not settings.backend_query_enabled:
        return apply_preflight_terminal_status(result)
    if provider_type.lower() != "tac":
        result["preflight_status"] = "UNSUPPORTED_PROVIDER"
        return apply_preflight_terminal_status(result)

    resolver = TenantBackendConfigResolver(settings)
    try:
        config = resolver.resolve(tenant_id=None)
    except BackendConfigError as exc:
        result.update(
            {
                "preflight_status": exc.code,
                "missing_config": _missing_config_from_error(str(exc)),
            }
        )
        return apply_preflight_terminal_status(result)

    from app.backends.factory import BackendProviderFactory

    if not (has_login_operator and (has_login_password or has_totp_secret)):
        result.update(
            {
                "preflight_status": "OK",
                "safe_to_probe": True,
                "login_attempted": False,
                "login_success": False,
            }
        )
        return apply_preflight_terminal_status(result)

    provider_factory = factory or BackendProviderFactory()
    result["preflight_status"] = "LOGIN_PENDING"
    result["login_attempted"] = True
    result["login_method"] = "otp" if has_totp_secret else "password"
    try:
        provider = provider_factory.create(config)
        provider.login_otp() if has_totp_secret else provider.login_password()
    except Exception as exc:
        result.update(
            {
                "preflight_status": "LOGIN_FAILED",
                "login_success": False,
                "error_type": type(exc).__name__,
                "error_message": sanitize_backend_text(str(exc), settings=settings),
            }
        )
        return apply_preflight_terminal_status(result)

    result.update(
        {
            "preflight_status": "OK",
            "login_success": True,
            "safe_to_probe": True,
        }
    )
    return apply_preflight_terminal_status(result)


def apply_preflight_terminal_status(result: dict) -> dict:
    status = result.get("preflight_status")
    if status == "OK":
        terminal_status, exit_code = "OK", 0
    elif status in {"DISABLED", "FAILED_CONFIG", "UNSUPPORTED_PROVIDER"}:
        terminal_status, exit_code = status, 2
    elif status == "LOGIN_FAILED":
        terminal_status, exit_code = status, 3
    else:
        terminal_status, exit_code = "FAILED", 1
    return {**result, "terminal_status": terminal_status, "exit_code": exit_code}


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
    if args.command == "preflight":
        result = _sanitize(run_preflight(settings), settings=settings)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
        return int(result.get("exit_code", 1))
    resolver = TenantBackendConfigResolver(settings)
    config = resolver.resolve(tenant_id=None)
    if getattr(args, "merchant_code", None):
        config.merchant_code = args.merchant_code
    from app.backends.factory import BackendProviderFactory

    provider = BackendProviderFactory().create(config)
    if args.command == "player":
        result = provider.query_player_user(args.username)
    elif args.command == "turnover":
        result = sanitize_turnover_query_result(provider.query_turnover_requirement(args.username))
    elif args.command == "deposit":
        result = {"records": provider.query_deposit(args.username, args.date_from, args.date_to)}
    elif args.command == "contribution":
        result = {"records": provider.query_player_contribution(args.username, args.date_from, args.date_to)}
    else:
        raise ValueError(f"unsupported command: {args.command}")
    print(json.dumps(_sanitize(result, settings=settings), ensure_ascii=False, indent=2, default=_json_default))
    return 0


def _sanitize(value, settings: Settings | None = None):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if not lowered.startswith("has_") and any(token in lowered for token in ("authorization", "password", "cookie", "token", "secret")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _sanitize(item, settings=settings)
        return redacted
    if isinstance(value, list):
        return [_sanitize(item, settings=settings) for item in value]
    if isinstance(value, str):
        return sanitize_backend_text(value, settings=settings)
    return value


def sanitize_backend_text(text: str, settings: Settings | None = None) -> str:
    redacted = text
    base_url = (getattr(settings, "backend_base_url", None) or "").strip().rstrip("/") if settings else ""
    if base_url:
        redacted = redacted.replace(base_url, "<redacted_backend_url>")
    patterns = [
        r"Authorization\s*[:=]\s*[^,\s}]+",
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        r"token\s*[:=]\s*[^,\s}]+",
        r"password\s*[:=]\s*[^,\s}]+",
        r"secret\s*[:=]\s*[^,\s}]+",
        r"cookie\s*[:=]\s*[^,\s}]+",
        r"https?://[^\s,)]+",
    ]
    for pattern in patterns:
        replacement = "<redacted_backend_url>" if pattern.startswith("https?") else "<redacted>"
        redacted = re.sub(pattern, replacement, redacted, flags=re.I)
    return redacted


def _settings_warnings() -> list[str]:
    if os.getenv("ENABLE_BACKEND_LOOKUP"):
        return ["ENABLE_BACKEND_LOOKUP is not used by this app; set BACKEND_QUERY_ENABLED=true"]
    return []


def _missing_config_from_error(message: str) -> list[str]:
    marker = "missing backend config:"
    if marker not in message:
        return []
    return [item.strip() for item in message.split(marker, 1)[1].split(",") if item.strip()]


def _json_default(value):
    if isinstance(value, (datetime, timedelta)):
        return str(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    raise SystemExit(main())
