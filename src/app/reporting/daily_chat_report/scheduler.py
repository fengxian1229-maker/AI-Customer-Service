import argparse
import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.reporting.daily_chat_report.runner import build_report_settings, run as run_daily_chat_report

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the scheduled LingXi daily chat report sender.")
    parser.add_argument("--once", action="store_true", help="Run one scheduled report immediately and exit.")
    parser.add_argument("--time", help="Daily local run time in HH:MM. Defaults to DAILY_CHAT_REPORT_TIME.")
    parser.add_argument("--timezone", help="Report timezone. Defaults to DAILY_CHAT_REPORT_TIMEZONE.")
    return parser


async def run_scheduler(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    settings = build_report_settings()
    if not settings.daily_chat_report_enabled:
        return {"worker": "daily_chat_report_scheduler", "status": "SKIPPED_DISABLED"}

    daily_time = args.time or settings.daily_chat_report_time
    timezone_name = args.timezone or settings.daily_chat_report_timezone
    _parse_daily_time(daily_time)
    ZoneInfo(timezone_name)

    if args.once:
        result = await _run_once()
        return {"worker": "daily_chat_report_scheduler", "status": "OK", "runs": [result]}

    runs = []
    while True:
        now = datetime.now(ZoneInfo(timezone_name))
        next_run = _next_run_at(now, daily_time, timezone_name)
        wait_seconds = max(0.0, (next_run - now).total_seconds())
        logger.info(
            "daily_chat_report_scheduler next_run=%s timezone=%s",
            next_run.isoformat(),
            timezone_name,
        )
        await asyncio.sleep(wait_seconds)
        try:
            result = await _run_once()
            runs.append(result)
            logger.info("daily_chat_report_scheduler completed result=%s", result)
        except Exception:
            logger.exception("daily_chat_report_scheduler run failed")


async def _run_once() -> dict:
    return await run_daily_chat_report(["--send"])


def _parse_daily_time(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("daily chat report time must use HH:MM format")
    hour = int(parts[0])
    minute = int(parts[1])
    return time(hour=hour, minute=minute)


def _next_run_at(now: datetime, daily_time: str, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    run_time = _parse_daily_time(daily_time)
    next_run = local_now.replace(hour=run_time.hour, minute=run_time.minute, second=0, microsecond=0)
    if next_run <= local_now:
        next_run += timedelta(days=1)
    return next_run


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = asyncio.run(run_scheduler(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"OK", "SKIPPED_DISABLED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
