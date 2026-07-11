import argparse
import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.channels.livechat.sender_client import LiveChatSenderClient
from app.channels.telegram.sender_client import TelegramSenderClient
from app.config.platforms import default_allowed_livechat_group_ids
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.reporting.daily_chat_report.aggregation import aggregate_threads
from app.reporting.daily_chat_report.pdf_renderer import render_daily_chat_report_pdf
from app.reporting.daily_chat_report.repository import (
    DailyChatReportAuditRepository,
    DailyChatReportReadRepository,
    LingxiLiveChatApiReportReadRepository,
    LingxiDailyChatReportReadRepository,
    LingxiLiveChatReportReadRepository,
)
from app.reporting.daily_chat_report.translation import GeminiTraditionalChineseTranslator, NullTranslator


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and optionally send the daily LiveChat transcript report.")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD. Defaults to previous day in report timezone.")
    parser.add_argument("--dry-run", action="store_true", help="Generate the PDF without sending it to Telegram.")
    parser.add_argument("--send", action="store_true", help="Send the generated PDF to Telegram.")
    parser.add_argument("--output-dir", help="Directory for generated PDFs.")
    parser.add_argument("--target-chat-id", help="Telegram chat id override.")
    parser.add_argument("--message-thread-id", type=int, help="Telegram topic id override.")
    parser.add_argument("--no-translate", action="store_true", help="Skip Gemini translation; useful for tests and dry runs.")
    parser.add_argument("--source", choices=("lingxi", "lingxi_db", "lingxi_archive", "ai_customer_service"), help="Report data source.")
    return parser


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    settings = build_report_settings()
    report_date = _report_date(args.date, settings.daily_chat_report_timezone)
    windows = _date_windows(report_date, settings.daily_chat_report_timezone)
    output_dir = Path(args.output_dir or settings.daily_chat_report_output_dir)
    output_path = output_dir / _report_filename(report_date, windows["display_start_at"], windows["display_end_at"])
    target_chat_id = args.target_chat_id or settings.daily_chat_report_target_chat_id
    message_thread_id = args.message_thread_id
    if message_thread_id is None:
        message_thread_id = settings.daily_chat_report_message_thread_id

    pool = await create_pool(settings)
    try:
        source = args.source or settings.daily_chat_report_source
        read_repository = _build_read_repository(pool, settings, source)
        messages, metadata, commands, states = await asyncio.gather(
            read_repository.fetch_messages(windows["query_start_at"], windows["query_end_at"]),
            read_repository.fetch_metadata(windows["query_start_at"], windows["query_end_at"]),
            read_repository.fetch_handoff_commands(windows["query_start_at"], windows["query_end_at"]),
            read_repository.fetch_states(windows["query_start_at"], windows["query_end_at"]),
        )
        threads = aggregate_threads(
            messages,
            metadata_rows=metadata,
            command_rows=commands,
            state_rows=states,
            allowed_group_ids=_parse_group_ids(settings.daily_chat_report_group_ids, default_allowed_livechat_group_ids(include_test=False)),
            excluded_group_ids=_parse_group_ids(settings.daily_chat_report_excluded_group_ids, {23}),
            require_agent_participation=source == "lingxi_archive",
            require_assistant_participation=source == "lingxi",
            bot_name="LingXi" if source in {"lingxi", "lingxi_archive"} else "Ai Jtest",
        )
        threads = _threads_for_display_timezone(threads, settings.daily_chat_report_timezone)
        translator = NullTranslator() if args.no_translate else GeminiTraditionalChineseTranslator(settings)
        render_daily_chat_report_pdf(
            threads,
            start_at=windows["display_start_at"],
            end_at=windows["display_end_at"],
            output_path=output_path,
            translator=translator,
        )

        result = {
            "worker": "daily_chat_report",
            "source": source,
            "report_date": report_date.isoformat(),
            "start_at": windows["display_start_at"].isoformat(sep=" "),
            "end_at": windows["display_end_at"].isoformat(sep=" "),
            "query_start_at": windows["query_start_at"].isoformat(sep=" "),
            "query_end_at": windows["query_end_at"].isoformat(sep=" "),
            "threads": len(threads),
            "pdf_path": str(output_path),
            "sent": False,
        }
        if args.send:
            if not target_chat_id:
                raise ValueError("DAILY_CHAT_REPORT_TARGET_CHAT_ID or --target-chat-id is required when --send is used")
            bot_token = _daily_chat_report_bot_token(settings)
            if not bot_token:
                raise ValueError("DAILY_CHAT_REPORT_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN is required when --send is used")
            audit = DailyChatReportAuditRepository(pool)
            await audit.ensure_table()
            started = await audit.start_once(
                report_date=report_date.isoformat(),
                target_chat_id=target_chat_id,
                message_thread_id=message_thread_id,
                pdf_path=str(output_path),
            )
            if not started["started"]:
                result["duplicate"] = True
                return result
            try:
                client = TelegramSenderClient(
                    bot_token,
                    api_base=settings.telegram_api_base,
                    timeout_seconds=settings.telegram_request_timeout_seconds,
                )
                response = client.send_document(
                    chat_id=target_chat_id,
                    document_path=str(output_path),
                    caption=f"LingXi 正式群組對話紀錄 {windows['display_start_at']:%Y%m%d}-{windows['display_end_at']:%Y%m%d}｜總數：{len(threads)}",
                    message_thread_id=message_thread_id,
                )
                telegram_message_id = ((response.get("result") or {}).get("message_id"))
                await audit.mark_sent(
                    report_date=report_date.isoformat(),
                    target_chat_id=target_chat_id,
                    message_thread_id=message_thread_id,
                    telegram_message_id=telegram_message_id,
                )
                result["sent"] = True
                result["telegram_message_id"] = telegram_message_id
            except Exception as exc:
                await audit.mark_failed(
                    report_date=report_date.isoformat(),
                    target_chat_id=target_chat_id,
                    message_thread_id=message_thread_id,
                    error_message=str(exc),
                )
                raise
        return result
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def build_report_settings() -> Settings:
    return Settings(
        livechat_agent_access_token=os.environ.get("LIVECHAT_AGENT_ACCESS_TOKEN", "unused-for-daily-chat-report"),
        livechat_account_id=os.environ.get("LIVECHAT_ACCOUNT_ID", "unused-for-daily-chat-report"),
    )


def _daily_chat_report_bot_token(settings: Settings) -> str | None:
    return settings.daily_chat_report_telegram_bot_token or settings.telegram_bot_token


def _build_read_repository(pool, settings: Settings, source: str):
    if source == "lingxi":
        return LingxiLiveChatApiReportReadRepository(
            pool,
            livechat_client=LiveChatSenderClient(
                settings.livechat_api_base,
                settings.livechat_account_id,
                settings.livechat_agent_access_token,
                agent_email=settings.livechat_agent_email,
            ),
            self_author_ids=settings.livechat_self_author_id_set,
        )
    if source == "lingxi_db":
        return LingxiLiveChatReportReadRepository(pool, self_author_ids=settings.livechat_self_author_id_set)
    if source == "lingxi_archive":
        return LingxiDailyChatReportReadRepository(pool, database=settings.daily_chat_report_lingxi_database)
    if source == "ai_customer_service":
        return DailyChatReportReadRepository(pool)
    raise ValueError(f"Unsupported daily chat report source: {source}")


def _report_date(value: str | None, timezone_name: str) -> date:
    if value:
        return date.fromisoformat(value)
    return (datetime.now(ZoneInfo(timezone_name)).date() - timedelta(days=1))


def _date_windows(report_date: date, timezone_name: str) -> dict[str, datetime]:
    tz = ZoneInfo(timezone_name)
    display_start = datetime.combine(report_date, time.min)
    display_end = datetime.combine(report_date + timedelta(days=1), time.min)
    local_start = display_start.replace(tzinfo=tz)
    local_end = display_end.replace(tzinfo=tz)
    return {
        "display_start_at": display_start,
        "display_end_at": display_end,
        "query_start_at": local_start.astimezone(UTC).replace(tzinfo=None),
        "query_end_at": local_end.astimezone(UTC).replace(tzinfo=None),
    }


def _report_filename(report_date: date, start_at: datetime, end_at: datetime) -> str:
    return f"LingXi_正式群組對話紀錄_{start_at:%Y%m%d}-{end_at:%Y%m%d}.pdf"


def _parse_group_ids(raw: str | None, default: set[int]) -> set[int]:
    if raw is None or not raw.strip():
        return set(default)
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


def _threads_for_display_timezone(threads, timezone_name: str):
    tz = ZoneInfo(timezone_name)
    converted = []
    for thread in threads:
        messages = [
            replace(
                message,
                occurred_at=_utc_naive_to_local_naive(message.occurred_at, tz),
                created_at=_utc_naive_to_local_naive(message.created_at, tz),
            )
            for message in thread.messages
        ]
        converted.append(
            replace(
                thread,
                start_at=_utc_naive_to_local_naive(thread.start_at, tz),
                end_at=_utc_naive_to_local_naive(thread.end_at, tz),
                messages=messages,
            )
        )
    return converted


def _utc_naive_to_local_naive(value: datetime | None, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(tz).replace(tzinfo=None)
