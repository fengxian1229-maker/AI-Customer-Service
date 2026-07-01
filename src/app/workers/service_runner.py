import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.core.settings import Settings
from app.workers import (
    external_command_worker,
    external_result_consumer,
    gateway_consumer,
    polling_receiver,
    sender_worker,
    telegram_reply_consumer,
)


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    interval_seconds: float
    run_once: Callable[[], Awaitable[dict]]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full AI customer-service worker chain.")
    parser.add_argument("--all", action="store_true", help="Enable every worker and the full automation loop.")
    parser.add_argument("--once", action="store_true", help="Run one service-runner iteration and exit.")
    parser.add_argument("--max-iterations", type=int, help="Maximum service-runner iterations before exiting.")

    error_group = parser.add_mutually_exclusive_group()
    error_group.add_argument("--stop-on-error", action="store_true", help="Stop the runner when any worker fails.")
    error_group.add_argument(
        "--continue-on-error",
        action="store_true",
        default=True,
        help="Log worker failures and continue running. This is the default.",
    )

    parser.add_argument("--groups", help="Comma-separated LiveChat group ids. Defaults to LIVECHAT_ALLOWED_GROUP_IDS.")
    parser.add_argument("--poll-seconds", type=float, help="Seconds between polling_receiver cycles.")
    parser.add_argument("--gateway-seconds", type=float, default=1.0, help="Seconds between gateway_consumer cycles.")
    parser.add_argument("--sender-seconds", type=float, default=1.0, help="Seconds between sender_worker cycles.")
    parser.add_argument(
        "--external-command-seconds",
        type=float,
        default=1.0,
        help="Seconds between external_command_worker cycles.",
    )
    parser.add_argument(
        "--external-result-seconds",
        type=float,
        default=1.0,
        help="Seconds between external_result_consumer cycles.",
    )
    parser.add_argument(
        "--telegram-reply-seconds",
        type=float,
        default=3.0,
        help="Seconds between telegram_reply_consumer cycles.",
    )

    parser.add_argument("--poll-limit", type=int, help="Maximum LiveChat chats to poll in one cycle.")
    parser.add_argument("--gateway-limit", type=int, default=20, help="Maximum inbound events to process in one cycle.")
    parser.add_argument("--sender-limit", type=int, default=20, help="Maximum outbound messages to send in one cycle.")
    parser.add_argument(
        "--external-command-limit",
        type=int,
        default=20,
        help="Maximum external commands to process in one cycle.",
    )
    parser.add_argument(
        "--external-result-limit",
        type=int,
        default=20,
        help="Maximum external command results to process in one cycle.",
    )
    parser.add_argument(
        "--telegram-reply-limit",
        type=int,
        default=20,
        help="Maximum Telegram updates to process in one cycle.",
    )
    return parser


def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    return asyncio.run(run_async(args))


async def run_async(args: argparse.Namespace) -> dict:
    if not args.all:
        return {
            "service_runner": "service_runner",
            "status": "FAILED_USAGE",
            "error": "--all is required to start the unified worker chain",
        }
    if args.max_iterations is not None and args.max_iterations <= 0:
        return {
            "service_runner": "service_runner",
            "status": "FAILED_USAGE",
            "error": "--max-iterations must be greater than 0",
        }

    settings = Settings()
    poll_seconds = args.poll_seconds if args.poll_seconds is not None else settings.poll_seconds
    poll_limit = args.poll_limit if args.poll_limit is not None else settings.poll_limit
    groups = polling_receiver.parse_group_ids(args.groups, settings.livechat_allowed_group_ids)
    stop_on_error = bool(args.stop_on_error)
    specs = _build_worker_specs(args, settings=settings, groups=groups, poll_limit=poll_limit, poll_seconds=poll_seconds)

    iterations = 0
    errors: list[dict] = []
    worker_runs = 0
    next_run_at = {spec.name: 0.0 for spec in specs}

    while args.max_iterations is None or iterations < args.max_iterations:
        iterations += 1
        due_specs = specs if args.once else _due_worker_specs(specs, next_run_at, now=time.monotonic())
        iteration_result = {"iteration": iterations, "workers": []}
        for spec in due_specs:
            worker_result = await _run_worker_once(spec, stop_on_error=stop_on_error)
            iteration_result["workers"].append(worker_result)
            worker_runs += 1
            next_run_at[spec.name] = time.monotonic() + spec.interval_seconds
            if worker_result.get("status") == "ERROR":
                errors.append(worker_result)
                if stop_on_error:
                    iteration_result["status"] = "STOPPED_ON_ERROR"
                    _print_iteration(iteration_result)
                    return _summary(iterations, worker_runs, errors, status="STOPPED_ON_ERROR")

        iteration_result["status"] = "OK"
        _print_iteration(iteration_result)
        if args.once:
            break
        if args.max_iterations is not None and iterations >= args.max_iterations:
            break
        await asyncio.sleep(_sleep_seconds(specs, next_run_at))

    return _summary(iterations, worker_runs, errors, status="OK")


def _build_worker_specs(
    args: argparse.Namespace,
    settings: Settings,
    groups: set[int],
    poll_limit: int,
    poll_seconds: float,
) -> list[WorkerSpec]:
    return [
        WorkerSpec(
            name="polling_receiver",
            interval_seconds=poll_seconds,
            run_once=lambda: polling_receiver.run_once(limit=poll_limit, groups=groups),
        ),
        WorkerSpec(
            name="gateway_consumer",
            interval_seconds=args.gateway_seconds,
            run_once=lambda: gateway_consumer.run_once(limit=args.gateway_limit),
        ),
        WorkerSpec(
            name="sender_worker",
            interval_seconds=args.sender_seconds,
            run_once=lambda: sender_worker.run_once(limit=args.sender_limit),
        ),
        WorkerSpec(
            name="external_command_worker",
            interval_seconds=args.external_command_seconds,
            run_once=lambda: external_command_worker.run_once(
                limit=args.external_command_limit,
                dry_run=False,
                emit_result=True,
                execute_human_handoff=True,
                execute_telegram=True,
                execute_backend=True,
            ),
        ),
        WorkerSpec(
            name="external_result_consumer",
            interval_seconds=args.external_result_seconds,
            run_once=lambda: external_result_consumer.run_once(limit=args.external_result_limit),
        ),
        WorkerSpec(
            name="telegram_reply_consumer",
            interval_seconds=args.telegram_reply_seconds,
            run_once=lambda: telegram_reply_consumer.run_once(
                limit=args.telegram_reply_limit,
                timeout=0,
                settings=settings,
            ),
        ),
    ]


def _due_worker_specs(specs: list[WorkerSpec], next_run_at: dict[str, float], now: float) -> list[WorkerSpec]:
    due = [spec for spec in specs if now >= next_run_at[spec.name]]
    return due or [min(specs, key=lambda spec: next_run_at[spec.name])]


async def _run_worker_once(spec: WorkerSpec, stop_on_error: bool) -> dict:
    try:
        result = await spec.run_once()
        return {"worker": spec.name, "status": "OK", "result": result}
    except Exception as exc:
        logger.exception("%s failed", spec.name)
        result = {
            "worker": spec.name,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "stop_on_error": stop_on_error,
        }
        return result


def _sleep_seconds(specs: list[WorkerSpec], next_run_at: dict[str, float]) -> float:
    now = time.monotonic()
    next_due = min(next_run_at[spec.name] for spec in specs)
    return max(0.0, next_due - now)


def _print_iteration(result: dict) -> None:
    print(json.dumps({"service_runner": result}, ensure_ascii=False, default=str))


def _summary(iterations: int, worker_runs: int, errors: list[dict], status: str) -> dict:
    return {
        "service_runner": "service_runner",
        "status": status,
        "iterations": iterations,
        "worker_runs": worker_runs,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    try:
        result = run(argv)
    except ValueError as exc:
        print(json.dumps({"service_runner": "service_runner", "status": "FAILED_USAGE", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] in {"FAILED_USAGE", "STOPPED_ON_ERROR"}:
        return 2 if result["status"] == "FAILED_USAGE" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_arg_parser", "run", "main"]
