from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    livechat_agent_access_token: str
    livechat_account_id: str
    livechat_agent_email: str | None = None
    livechat_api_base: str = "https://api.livechatinc.com/v3.6"
    livechat_self_author_ids: str = ""
    livechat_handoff_target_group_id: int | None = None
    livechat_handoff_ignore_agents_availability: bool = True
    livechat_handoff_ignore_requester_presence: bool = True
    livechat_handoff_enabled: bool = False
    livechat_image_text_fallback: bool = False
    livechat_webhook_secret: str | None = None
    livechat_webhook_enabled: bool = False
    webhook_server_host: str = "0.0.0.0"
    webhook_server_port: int = 8000
    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_sop_enabled: bool = False
    telegram_test_group: str | None = None
    telegram_finance_group: str | None = None
    telegram_sop_target_chat_id: str | None = None
    telegram_sop_message_thread_id: int | None = None
    telegram_force_no_topic: bool = False
    telegram_request_timeout_seconds: float = 15.0
    telegram_upload_attachments_via_download: bool = True
    telegram_attachment_download_timeout_seconds: float = 15.0
    telegram_attachment_max_bytes: int = 10485760
    backend_query_enabled: bool = False
    backend_provider_type: str | None = None
    backend_base_url: str | None = None
    backend_authorization: str | None = None
    backend_merchant_code: str | None = None
    backend_login_operator: str | None = None
    backend_login_password: str | None = None
    backend_login_merchant: str | None = None
    backend_request_timeout_seconds: float = 20.0
    backend_default_lookback_days: int = 30
    backend_fallback_lookback_days: int = 90

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "ai_customer_service"

    poll_seconds: int = 5
    poll_limit: int = 20
    livechat_allowed_group_ids: str = ""
    langgraph_checkpoint_mode: str = "off"
    langgraph_checkpoint_setup_on_start: bool = False
    llm_provider: str = "gemini"
    llm_rewrite_shadow_enabled: bool = False
    llm_rewrite_fallback_enabled: bool = False
    llm_intent_shadow_enabled: bool = False
    llm_intent_fallback_enabled: bool = False
    llm_intent_min_confidence: float = 0.75
    llm_intent_fallback_to_deterministic: bool = True
    llm_sop_slot_enabled: bool = True
    llm_sop_slot_min_confidence: float = 0.70
    llm_sop_slot_fallback_to_deterministic: bool = True
    llm_final_reply_enabled: bool = True
    llm_final_reply_min_confidence: float = 0.70
    llm_final_reply_fallback_enabled: bool = True
    llm_final_reply_streaming_enabled: bool = True
    llm_final_reply_preview_enabled: bool = False
    llm_final_reply_preview_min_chars: int = 80
    llm_final_reply_preview_interval_ms: int = 700
    llm_final_reply_preview_min_delta_chars: int = 24
    llm_final_reply_preview_max_updates: int = 12
    livechat_typing_indicator_enabled: bool = True
    livechat_thinking_indicator_enabled: bool = False
    language_detection_enabled: bool = True
    language_detection_min_confidence: float = 0.70
    tenant_persona_default_language: str = "zh-Hans"
    tenant_supported_languages: str = "zh-Hans,zh-Hant,en,es,tl,th,my,ms"
    language_fallback: str = "zh-Hans"
    language_persist_to_slot_memory: bool = True
    tenant_persona_tone: str = "polite"
    tenant_persona_assistant_name: str | None = None
    tenant_persona_brand_name: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_project: str = "project-gemini-0306"
    gemini_location: str = "global"
    gemini_temperature: float = 1.0
    gemini_max_tokens: int | None = None
    gemini_timeout_seconds: float | None = None
    gemini_max_retries: int = 2
    gemini_vertexai: bool = True

    @field_validator("livechat_handoff_target_group_id", mode="before")
    @classmethod
    def parse_livechat_handoff_target_group_id(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if not stripped.isdigit():
                raise ValueError("livechat_handoff_target_group_id must be a positive integer")
            value = int(stripped)
        if isinstance(value, int):
            if value <= 0:
                raise ValueError("livechat_handoff_target_group_id must be a positive integer")
            return value
        return value

    @property
    def livechat_self_author_id_set(self) -> set[str]:
        return {
            item.strip()
            for item in self.livechat_self_author_ids.split(",")
            if item.strip()
        }

    @property
    def livechat_allowed_group_id_set(self) -> set[int]:
        return {
            int(item.strip())
            for item in self.livechat_allowed_group_ids.split(",")
            if item.strip()
        }

    @property
    def mysql_checkpoint_dsn(self) -> str:
        password = quote_plus(self.mysql_password)
        return (
            f"mysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )
