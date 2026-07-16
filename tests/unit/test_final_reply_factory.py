from app.core.settings import Settings
from app.llm.final_reply_provider import FinalReplyLLMProvider
from app.services.final_reply_factory import build_final_reply_service_from_settings


def test_build_final_reply_service_from_settings_wires_failover_model_and_timeouts():
    settings = Settings(
        livechat_agent_access_token="unused-for-test",
        livechat_account_id="unused-for-test",
        llm_provider="gemini",
        llm_final_reply_enabled=True,
        llm_final_reply_failover_model="gemini-3.1-flash-lite",
        llm_final_reply_timeout_seconds=12.5,
        llm_final_reply_failover_timeout_seconds=6.25,
    )

    service = build_final_reply_service_from_settings(settings)

    assert service is not None
    assert isinstance(service.provider, FinalReplyLLMProvider)
    assert service.provider.model_name is None
    assert isinstance(service.failover_provider, FinalReplyLLMProvider)
    assert service.failover_provider.model_name == "gemini-3.1-flash-lite"
    assert service.timeout_seconds == 12.5
    assert service.failover_timeout_seconds == 6.25
