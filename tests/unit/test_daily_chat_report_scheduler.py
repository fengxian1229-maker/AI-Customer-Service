import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo


def test_daily_chat_report_default_time_is_9am():
    from app.core.settings import Settings

    settings = Settings(livechat_agent_access_token="token", livechat_account_id="account")

    assert settings.daily_chat_report_time == "09:00"


def test_empty_daily_chat_report_message_thread_id_is_none(monkeypatch):
    from app.core.settings import Settings

    monkeypatch.setenv("DAILY_CHAT_REPORT_MESSAGE_THREAD_ID", "")

    settings = Settings(livechat_agent_access_token="token", livechat_account_id="account")

    assert settings.daily_chat_report_message_thread_id is None


def test_daily_chat_report_has_separate_telegram_bot_token(monkeypatch):
    from app.core.settings import Settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "sop-token")
    monkeypatch.setenv("DAILY_CHAT_REPORT_TELEGRAM_BOT_TOKEN", "report-token")

    settings = Settings(livechat_agent_access_token="token", livechat_account_id="account")

    assert settings.telegram_bot_token == "sop-token"
    assert settings.daily_chat_report_telegram_bot_token == "report-token"


def test_daily_chat_report_bot_token_prefers_report_token_and_falls_back():
    from app.core.settings import Settings
    from app.reporting.daily_chat_report.runner import _daily_chat_report_bot_token

    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        telegram_bot_token="sop-token",
        daily_chat_report_telegram_bot_token="report-token",
    )

    assert _daily_chat_report_bot_token(settings) == "report-token"

    fallback_settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
        telegram_bot_token="sop-token",
    )

    assert _daily_chat_report_bot_token(fallback_settings) == "sop-token"


def test_next_run_at_uses_configured_local_time_before_scheduled_time():
    from app.reporting.daily_chat_report.scheduler import _next_run_at

    now = datetime(2026, 7, 10, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    next_run = _next_run_at(now, "09:00", "Asia/Shanghai")

    assert next_run == datetime(2026, 7, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_next_run_at_rolls_to_tomorrow_after_scheduled_time():
    from app.reporting.daily_chat_report.scheduler import _next_run_at

    now = datetime(2026, 7, 10, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai"))

    next_run = _next_run_at(now, "09:00", "Asia/Shanghai")

    assert next_run == datetime(2026, 7, 11, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_scheduler_once_uses_send_path(monkeypatch):
    from app.core.settings import Settings
    from app.reporting.daily_chat_report import scheduler

    calls = []

    async def fake_run_daily_chat_report(argv):
        calls.append(argv)
        return {"argv": argv, "sent": True}

    monkeypatch.setattr(
        scheduler,
        "build_report_settings",
        lambda: Settings(
            livechat_agent_access_token="token",
            livechat_account_id="account",
            daily_chat_report_enabled=True,
        ),
    )
    monkeypatch.setattr(scheduler, "run_daily_chat_report", fake_run_daily_chat_report)

    result = asyncio.run(scheduler.run_scheduler(["--once"]))

    assert calls == [["--send"]]
    assert result["status"] == "OK"
    assert result["runs"] == [{"argv": ["--send"], "sent": True}]
