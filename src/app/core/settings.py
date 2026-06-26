from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    livechat_agent_access_token: str
    livechat_account_id: str
    livechat_api_base: str = "https://api.livechatinc.com/v3.6"
    livechat_self_author_ids: str = ""

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
    llm_provider: str = "off"
    llm_rewrite_shadow_enabled: bool = False
    llm_rewrite_fallback_enabled: bool = False
    llm_intent_shadow_enabled: bool = False
    llm_intent_fallback_enabled: bool = False
    llm_intent_min_confidence: float = 0.75

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
