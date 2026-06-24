from app.core.settings import Settings


def test_settings_defaults():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
    )

    assert settings.livechat_api_base == "https://api.livechatinc.com/v3.6"
    assert settings.poll_seconds == 5
    assert settings.mysql_port == 3306


def test_build_app_has_health_route():
    from app.api.main import build_app

    app = build_app()

    paths = {route.path for route in app.routes}
    assert "/healthz" in paths


def test_load_sql_files_in_order():
    from pathlib import Path

    from app.db.bootstrap import load_sql_files

    files = load_sql_files(Path("sql"))

    assert [item.name for item in files] == [
        "001_inbound_events.sql",
        "002_conversation_states.sql",
        "003_outbound_messages.sql",
    ]
