import argparse
import asyncio
import json
import logging
import signal
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.channels.livechat.sender_client import LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ConversationRepository,
    ExternalCommandRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    InboundEventRepository,
    OutboundMessageRepository,
)
from app.services.final_reply_factory import build_final_reply_service_from_settings
from app.workers import (
    external_command_worker,
    external_result_consumer,
    gateway_consumer,
    livechat_idle_timer,
    polling_receiver,
    sender_worker,
    telegram_reply_consumer,
)


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
WORKER_NAMES = [
    "polling_receiver",
    "gateway_consumer",
    "sender_worker",
    "external_command_worker",
    "external_result_consumer",
    "telegram_reply_consumer",
    "livechat_idle_timer",
]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceRunnerConfig:
    poll_seconds: float
    gateway_seconds: float
    sender_seconds: float
    external_command_seconds: float
    external_result_seconds: float
    telegram_reply_seconds: float
    livechat_idle_timer_seconds: float
    poll_limit: int
    gateway_limit: int
    sender_limit: int
    external_command_limit: int
    external_result_limit: int
    telegram_reply_limit: int
    livechat_idle_timer_limit: int
    max_iterations: int | None
    stop_on_error: bool
    shutdown_timeout_seconds: float


@dataclass
class ServiceRunnerContext:
    settings: Settings
    pool: Any
    polling_client: LiveChatSenderClient
    sender_client: LiveChatSenderClient
    groups: set[int]
    config: ServiceRunnerConfig
    external_command_worker_id: str = field(default_factory=lambda: f"service-runner-external-command-{socket.gethostname()}")
    external_result_worker_id: str = field(default_factory=lambda: f"service-runner-external-result-{socket.gethostname()}")


@dataclass
class RunnerSummary:
    workers: dict[str, dict] = field(default_factory=lambda: {name: _empty_worker_summary() for name in WORKER_NAMES})
    errors: list[dict] = field(default_factory=list)

    def record_success(self, name: str, result: dict, elapsed_ms: float) -> None:
        worker = self.workers.setdefault(name, _empty_worker_summary())
        worker["iterations"] += 1
        worker["successes"] += 1
        worker["last_elapsed_ms"] = elapsed_ms
        worker["last_result"] = result

    def record_error(self, name: str, exc: Exception, elapsed_ms: float) -> dict:
        worker = self.workers.setdefault(name, _empty_worker_summary())
        worker["iterations"] += 1
        worker["errors"] += 1
        worker["last_elapsed_ms"] = elapsed_ms
        error = {
            "worker": name,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "elapsed_ms": elapsed_ms,
        }
        self.errors.append(error)
        return error

    def to_result(self, status: str, shutdown_reason: str | None = None) -> dict:
        return {
            "service_runner": "service_runner",
            "status": status,
            "mode": "all",
            "shutdown_reason": shutdown_reason,
            "workers": self.workers,
            "errors": self.errors,
        }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full AI customer-service worker chain.")
    parser.add_argument("--all", action="store_true", help="Enable every worker and the full automation loop.")
    parser.add_argument("--once", action="store_true", help="Run every enabled worker once and exit.")
    parser.add_argument("--max-iterations", type=int, help="Maximum iterations per enabled worker before exiting.")

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
    parser.add_argument("--external-command-seconds", type=float, default=1.0)
    parser.add_argument("--external-result-seconds", type=float, default=1.0)
    parser.add_argument("--telegram-reply-seconds", type=float, default=3.0)
    parser.add_argument("--livechat-idle-timer-seconds", type=float, default=5.0)

    parser.add_argument("--poll-limit", type=int, help="Maximum LiveChat chats to poll in one cycle.")
    parser.add_argument("--gateway-limit", type=int, default=20)
    parser.add_argument("--sender-limit", type=int, default=20)
    parser.add_argument("--external-command-limit", type=int, default=20)
    parser.add_argument("--external-result-limit", type=int, default=20)
    parser.add_argument("--telegram-reply-limit", type=int, default=20)
    parser.add_argument("--livechat-idle-timer-limit", type=int, default=20)
    parser.add_argument(
        "--shutdown-timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for running worker tasks to stop after shutdown is requested.",
    )
    return parser


def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    return asyncio.run(run_async(args))


async def run_async(args: argparse.Namespace) -> dict:
    usage_error = _validate_usage(args)
    if usage_error:
        return usage_error

    settings = Settings()
    try:
        groups = polling_receiver.parse_group_ids(args.groups, settings.livechat_allowed_group_ids)
    except ValueError as exc:
        return _failed_usage(str(exc))

    config = build_config(args, settings)
    preflight = preflight_all(settings, groups)
    if not preflight["ok"]:
        _log_event("service_runner.preflight.failed", missing=preflight["missing"], warnings=preflight["warnings"])
        return {
            "service_runner": "service_runner",
            "status": "FAILED_PREFLIGHT",
            "mode": "all",
            "missing": preflight["missing"],
            "warnings": preflight["warnings"],
            "configured": preflight["configured"],
        }

    _log_event("service_runner.preflight.ok", configured=preflight["configured"])
    pool = await create_pool(settings)
    try:
        context = build_context(settings=settings, pool=pool, groups=groups, config=config)
        return await run_all_workers(context)
    except asyncio.CancelledError:
        _log_event("service_runner.shutdown", status="CANCELLED", shutdown_reason="cancelled", worker_iterations={}, errors_count=0)
        return {
            "service_runner": "service_runner",
            "status": "CANCELLED",
            "mode": "all",
            "shutdown_reason": "cancelled",
            "workers": {},
            "errors": [],
        }
    finally:
        pool.close()
        await pool.wait_closed()


def build_config(args: argparse.Namespace, settings: Settings) -> ServiceRunnerConfig:
    max_iterations = 1 if args.once else args.max_iterations
    return ServiceRunnerConfig(
        poll_seconds=args.poll_seconds if args.poll_seconds is not None else settings.poll_seconds,
        gateway_seconds=args.gateway_seconds,
        sender_seconds=args.sender_seconds,
        external_command_seconds=args.external_command_seconds,
        external_result_seconds=args.external_result_seconds,
        telegram_reply_seconds=args.telegram_reply_seconds,
        livechat_idle_timer_seconds=args.livechat_idle_timer_seconds,
        poll_limit=args.poll_limit if args.poll_limit is not None else settings.poll_limit,
        gateway_limit=args.gateway_limit,
        sender_limit=args.sender_limit,
        external_command_limit=args.external_command_limit,
        external_result_limit=args.external_result_limit,
        telegram_reply_limit=args.telegram_reply_limit,
        livechat_idle_timer_limit=args.livechat_idle_timer_limit,
        max_iterations=max_iterations,
        stop_on_error=bool(args.stop_on_error),
        shutdown_timeout_seconds=args.shutdown_timeout_seconds,
    )


def build_context(settings: Settings, pool: Any, groups: set[int], config: ServiceRunnerConfig) -> ServiceRunnerContext:
    client_args = _livechat_client_args(settings)
    polling_client = LiveChatSenderClient(*client_args)
    sender_client = LiveChatSenderClient(*client_args)
    return ServiceRunnerContext(
        settings=settings,
        pool=pool,
        polling_client=polling_client,
        sender_client=sender_client,
        groups=groups,
        config=config,
    )


def _livechat_client_args(settings: Settings) -> tuple:
    args = (
        settings.livechat_api_base,
        settings.livechat_account_id,
        settings.livechat_agent_access_token,
    )
    agent_email = getattr(settings, "livechat_agent_email", None)
    if agent_email:
        return (*args, agent_email)
    return args


def preflight_all(settings: Settings, groups: set[int]) -> dict:
    missing = []
    warnings = []

    _require_text(settings.livechat_agent_access_token, "LIVECHAT_AGENT_ACCESS_TOKEN", missing)
    _require_text(settings.livechat_account_id, "LIVECHAT_ACCOUNT_ID", missing)
    _require_text(settings.livechat_api_base, "LIVECHAT_API_BASE", missing)
    _require_text(settings.mysql_host, "MYSQL_HOST", missing)
    _require_text(settings.mysql_user, "MYSQL_USER", missing)
    _require_text(settings.mysql_database, "MYSQL_DATABASE", missing)
    if not groups:
        missing.append("LIVECHAT_ALLOWED_GROUP_IDS")
    if not settings.livechat_self_author_id_set:
        warnings.append("LIVECHAT_SELF_AUTHOR_IDS is empty; self messages may not be filtered")

    if not settings.telegram_sop_enabled:
        missing.append("TELEGRAM_SOP_ENABLED")
    _require_text(settings.telegram_bot_token, "TELEGRAM_BOT_TOKEN", missing)
    if not _telegram_target_configured(settings):
        missing.append("TELEGRAM_SOP_TARGET_CHAT_ID_OR_GROUP")

    if not settings.backend_query_enabled:
        missing.append("BACKEND_QUERY_ENABLED")
    _require_text(settings.backend_provider_type, "BACKEND_PROVIDER_TYPE", missing)

    if not settings.livechat_handoff_enabled:
        missing.append("LIVECHAT_HANDOFF_ENABLED")
    if settings.livechat_handoff_target_group_id is None:
        missing.append("LIVECHAT_HANDOFF_TARGET_GROUP_ID")

    return {
        "ok": not missing,
        "missing": sorted(dict.fromkeys(missing)),
        "warnings": warnings,
        "configured": {
            "livechat": bool(settings.livechat_agent_access_token and settings.livechat_account_id and settings.livechat_api_base),
            "mysql": bool(settings.mysql_host and settings.mysql_user and settings.mysql_database),
            "polling_groups": bool(groups),
            "telegram": bool(settings.telegram_sop_enabled and settings.telegram_bot_token and _telegram_target_configured(settings)),
            "backend": bool(settings.backend_query_enabled and settings.backend_provider_type),
            "human_handoff": bool(settings.livechat_handoff_enabled and settings.livechat_handoff_target_group_id is not None),
        },
    }


async def polling_tick(context: ServiceRunnerContext) -> dict:
    result = await polling_receiver.run_polling_cycle(
        client=context.polling_client,
        repository=InboundEventRepository(context.pool),
        self_author_ids=context.settings.livechat_self_author_id_set,
        limit=context.config.poll_limit,
        allowed_group_ids=context.groups,
    )
    return {"worker": "polling_receiver", **_pick(result, ["listed", "matched_group", "inserted", "duplicates", "ignored"])}


async def gateway_tick(context: ServiceRunnerContext) -> dict:
    result = await gateway_consumer.process_next_batch(
        context.pool,
        limit=context.config.gateway_limit,
        checkpoint_mode=context.settings.langgraph_checkpoint_mode,
        settings=context.settings,
    )
    return {
        "worker": "gateway_consumer",
        "processed": result["processed"],
        "failed": result["failed"],
        "enqueued": result["enqueued"],
        "failures": result.get("failures", []),
        "llm": result.get("llm"),
    }


async def sender_tick(context: ServiceRunnerContext) -> dict:
    results = await sender_worker.process_next_batch(
        context.pool,
        context.sender_client,
        limit=context.config.sender_limit,
    )
    return {
        "worker": "sender_worker",
        "processed": len(results),
        "sent": sum(1 for result in results if result.get("status") == "SENT"),
        "failed": sum(1 for result in results if result.get("status") != "SENT"),
        "retryable": sum(1 for result in results if result.get("status") == "RETRYABLE"),
    }


async def external_command_tick(context: ServiceRunnerContext) -> dict:
    results = await external_command_worker.process_pending_commands(
        ExternalCommandRepository(context.pool),
        result_repository=ExternalCommandResultRepository(context.pool),
        conversation_repository=ConversationRepository(context.pool),
        outbound_repository=OutboundMessageRepository(context.pool),
        limit=context.config.external_command_limit,
        dry_run=False,
        emit_result=True,
        execute_human_handoff=True,
        execute_telegram=True,
        execute_backend=True,
        settings=context.settings,
        worker_id=context.external_command_worker_id,
    )
    summary = external_command_worker.summarize_results(results)
    return {
        "worker": "external_command_worker",
        "processed": summary["processed"],
        "sent": summary["sent"],
        "failed": summary["failed"],
        "retryable": summary["retryable"],
        "emitted_result": summary["results_emitted"],
        "skipped": summary["skipped"],
        "blocked": summary["blocked"],
    }


async def external_result_tick(context: ServiceRunnerContext) -> dict:
    result_repository = ExternalCommandResultRepository(context.pool)
    conversation_repository = ConversationRepository(context.pool)
    outbound_repository = OutboundMessageRepository(context.pool)
    final_reply_service = build_final_reply_service_from_settings(context.settings)
    results = await external_result_consumer.process_pending_results(
        result_repository=result_repository,
        conversation_repository=conversation_repository,
        outbound_repository=outbound_repository,
        transaction_repository=ExternalResultTransactionRepository(
            context.pool,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            result_repository=result_repository,
        ),
        limit=context.config.external_result_limit,
        worker_id=context.external_result_worker_id,
        final_reply_service=final_reply_service,
        llm_final_reply_enabled=getattr(context.settings, "llm_final_reply_enabled", False),
    )
    return {
        "worker": "external_result_consumer",
        "processed": len(results),
        "succeeded": sum(1 for result in results if result.get("status") == "PROCESSED"),
        "failed": sum(1 for result in results if result.get("status") == "FAILED"),
    }


async def telegram_reply_tick(context: ServiceRunnerContext) -> dict:
    return await telegram_reply_consumer.process_telegram_updates_once(
        pool=context.pool,
        settings=context.settings,
        limit=context.config.telegram_reply_limit,
        timeout=0,
    )


async def livechat_idle_timer_tick(context: ServiceRunnerContext) -> dict:
    results = await livechat_idle_timer.process_idle_conversations(
        context.pool,
        context.sender_client,
        limit=context.config.livechat_idle_timer_limit,
    )
    return {"worker": "livechat_idle_timer", **livechat_idle_timer.summarize_results(results)}


async def run_all_workers(context: ServiceRunnerContext) -> dict:
    summary = RunnerSummary()
    stop_event = asyncio.Event()
    shutdown_reason = {"value": None}
    loop = asyncio.get_running_loop()
    registered_signals = install_signal_handlers(loop, stop_event, shutdown_reason)
    worker_defs = [
        ("polling_receiver", context.config.poll_seconds, lambda: polling_tick(context)),
        ("gateway_consumer", context.config.gateway_seconds, lambda: gateway_tick(context)),
        ("sender_worker", context.config.sender_seconds, lambda: sender_tick(context)),
        ("external_command_worker", context.config.external_command_seconds, lambda: external_command_tick(context)),
        ("external_result_consumer", context.config.external_result_seconds, lambda: external_result_tick(context)),
        ("telegram_reply_consumer", context.config.telegram_reply_seconds, lambda: telegram_reply_tick(context)),
        ("livechat_idle_timer", context.config.livechat_idle_timer_seconds, lambda: livechat_idle_timer_tick(context)),
    ]
    _log_event("service_runner.start", mode="all", enabled_workers=WORKER_NAMES)
    tasks = [
        asyncio.create_task(
            run_periodic_worker(
                name=name,
                interval_seconds=interval,
                tick=tick,
                stop_event=stop_event,
                max_iterations=context.config.max_iterations,
                stop_on_error=context.config.stop_on_error,
                summary=summary,
                shutdown_reason=shutdown_reason,
            ),
            name=f"service_runner:{name}",
        )
        for name, interval, tick in worker_defs
    ]
    status = "OK"
    stop_waiter = asyncio.create_task(stop_event.wait(), name="service_runner:stop_event")
    try:
        pending_workers: set[asyncio.Task] = set(tasks)
        while pending_workers:
            done, pending = await asyncio.wait(
                [*pending_workers, stop_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            pending_workers = {task for task in pending_workers if not task.done()}
            if stop_waiter in done or stop_event.is_set():
                break
            if not pending_workers:
                break

        if stop_event.is_set() and pending_workers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_workers, return_exceptions=True),
                    timeout=context.config.shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                status = "SHUTDOWN_TIMEOUT"
                pending_names = _pending_worker_names(pending_workers)
                _log_event(
                    "service_runner.shutdown.timeout",
                    timeout_seconds=context.config.shutdown_timeout_seconds,
                    pending_workers=pending_names,
                )
                for task in pending_workers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*pending_workers, return_exceptions=True)
        elif pending_workers:
            await asyncio.gather(*pending_workers, return_exceptions=True)
    except asyncio.CancelledError:
        status = "CANCELLED"
        if not shutdown_reason.get("value"):
            shutdown_reason["value"] = "cancelled"
        stop_event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        stop_waiter.cancel()
        await asyncio.gather(stop_waiter, return_exceptions=True)
        remove_signal_handlers(loop, registered_signals)
    if context.config.stop_on_error and summary.errors:
        status = "STOPPED_ON_ERROR"
    elif status == "OK" and str(shutdown_reason.get("value") or "").startswith("signal:"):
        status = "CANCELLED"
    result = summary.to_result(status, shutdown_reason=shutdown_reason["value"])
    _log_event(
        "service_runner.shutdown",
        status=status,
        shutdown_reason=shutdown_reason["value"],
        worker_iterations={name: data["iterations"] for name, data in summary.workers.items()},
        errors_count=len(summary.errors),
    )
    return result


async def run_periodic_worker(
    name: str,
    interval_seconds: float,
    tick: Callable[[], Awaitable[dict]],
    stop_event: asyncio.Event,
    max_iterations: int | None,
    stop_on_error: bool,
    summary: RunnerSummary,
    shutdown_reason: dict | None = None,
) -> None:
    iteration = 0
    _log_event("service_runner.worker.start", worker=name, interval_seconds=interval_seconds, max_iterations=max_iterations)
    while not stop_event.is_set():
        if max_iterations is not None and iteration >= max_iterations:
            return
        iteration += 1
        started = time.monotonic()
        try:
            result = await tick()
            elapsed_ms = _elapsed_ms(started)
            summary.record_success(name, result, elapsed_ms)
            _log_event(
                "service_runner.worker.tick.end",
                worker=name,
                iteration=iteration,
                elapsed_ms=elapsed_ms,
                result=result,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed_ms = _elapsed_ms(started)
            error = summary.record_error(name, exc, elapsed_ms)
            _log_event(
                "service_runner.worker.tick.error",
                worker=name,
                iteration=iteration,
                error_type=error["error_type"],
                error_message=error["error_message"],
                elapsed_ms=elapsed_ms,
            )
            if stop_on_error:
                if shutdown_reason is not None and not shutdown_reason.get("value"):
                    shutdown_reason["value"] = "stop_on_error"
                stop_event.set()
                return
        if max_iterations is not None and iteration >= max_iterations:
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    try:
        result = run(argv)
    except KeyboardInterrupt:
        result = {
            "service_runner": "service_runner",
            "status": "CANCELLED",
            "mode": "all",
            "shutdown_reason": "keyboard_interrupt",
            "workers": {},
            "errors": [],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "OK":
        return 0
    if result["status"] in {"FAILED_USAGE", "FAILED_PREFLIGHT"}:
        return 2
    if result["status"] == "STOPPED_ON_ERROR":
        return 1
    if result["status"] == "CANCELLED":
        return 130
    if result["status"] == "SHUTDOWN_TIMEOUT":
        return 1
    return 1


def _validate_usage(args: argparse.Namespace) -> dict | None:
    if not args.all:
        return _failed_usage("--all is required to start the unified worker chain")
    if args.max_iterations is not None and args.max_iterations <= 0:
        return _failed_usage("--max-iterations must be greater than 0")
    for name in [
        "poll_seconds",
        "gateway_seconds",
        "sender_seconds",
        "external_command_seconds",
        "external_result_seconds",
        "telegram_reply_seconds",
        "livechat_idle_timer_seconds",
    ]:
        value = getattr(args, name)
        if value is not None and value < 0:
            return _failed_usage(f"--{name.replace('_', '-')} must be greater than or equal to 0")
    if args.shutdown_timeout_seconds < 0:
        return _failed_usage("--shutdown-timeout-seconds must be greater than or equal to 0")
    return None


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    stop_event: asyncio.Event,
    shutdown_reason: dict,
) -> list[int]:
    registered_signals = []
    for sig in [signal.SIGTERM, signal.SIGINT]:
        try:
            loop.add_signal_handler(sig, _request_shutdown, stop_event, shutdown_reason, f"signal:{sig.name}")
            registered_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            continue
    return registered_signals


def remove_signal_handlers(loop: asyncio.AbstractEventLoop, registered_signals: list[int]) -> None:
    for sig in registered_signals:
        try:
            loop.remove_signal_handler(sig)
        except (NotImplementedError, RuntimeError):
            continue


def _request_shutdown(stop_event: asyncio.Event, shutdown_reason: dict, reason: str) -> None:
    if not stop_event.is_set():
        if not shutdown_reason.get("value"):
            shutdown_reason["value"] = reason
        _log_event("service_runner.shutdown.requested", reason=shutdown_reason["value"])
        stop_event.set()


def _failed_usage(error: str) -> dict:
    return {"service_runner": "service_runner", "status": "FAILED_USAGE", "mode": "all", "error": error}


def _empty_worker_summary() -> dict:
    return {"iterations": 0, "successes": 0, "errors": 0, "last_result": None}


def _require_text(value: Any, env_name: str, missing: list[str]) -> None:
    if not str(value or "").strip():
        missing.append(env_name)


def _telegram_target_configured(settings: Settings) -> bool:
    return any(
        str(value or "").strip()
        for value in [
            settings.telegram_sop_target_chat_id,
            settings.telegram_finance_group,
            settings.telegram_test_group,
        ]
    )


def _pick(payload: dict, keys: list[str]) -> dict:
    return {key: payload.get(key) for key in keys if key in payload}


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


def _pending_worker_names(tasks: set[asyncio.Task]) -> list[str]:
    names = []
    for task in tasks:
        name = task.get_name()
        names.append(name.split("service_runner:", 1)[-1])
    return sorted(names)


def _log_event(event: str, **kwargs) -> None:
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ServiceRunnerConfig",
    "ServiceRunnerContext",
    "RunnerSummary",
    "build_arg_parser",
    "preflight_all",
    "polling_tick",
    "gateway_tick",
    "sender_tick",
    "external_command_tick",
    "external_result_tick",
    "telegram_reply_tick",
    "run_periodic_worker",
    "install_signal_handlers",
    "remove_signal_handlers",
    "run",
    "main",
]
