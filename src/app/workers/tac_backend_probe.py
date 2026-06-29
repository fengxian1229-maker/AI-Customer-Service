import argparse
import json
from datetime import datetime, timedelta

from app.backends.resolver import TenantBackendConfigResolver
from app.core.settings import Settings
from app.services.backend_query_service import sanitize_turnover_query_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only TAC backend probe.")
    subparsers = parser.add_subparsers(dest="command", required=True)

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


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
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
    print(json.dumps(_sanitize(result), ensure_ascii=False, indent=2, default=_json_default))
    return 0


def _sanitize(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("authorization", "password", "cookie", "token")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _sanitize(item)
        return redacted
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _json_default(value):
    if isinstance(value, (datetime, timedelta)):
        return str(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    raise SystemExit(main())
