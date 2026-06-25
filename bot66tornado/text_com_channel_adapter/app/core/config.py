from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ai-customer-service-gateway"
    app_env: str = "local"
    app_debug: bool = False

    text_com_webhook_secret: str | None = None
    text_com_agent_auth_token: str | None = None
    text_com_api_base_url: str = "https://api.livechatinc.com/v3.6"
    text_com_ignored_author_ids: str = ""

    default_tenant_id: str = "tenant_default"

    @property
    def ignored_author_ids(self) -> set[str]:
        return {
            item.strip()
            for item in self.text_com_ignored_author_ids.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
