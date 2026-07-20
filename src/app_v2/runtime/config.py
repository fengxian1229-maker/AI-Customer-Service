from pydantic_settings import BaseSettings, SettingsConfigDict


class V2Settings(BaseSettings):
    """Bootstrap settings whose environment variables are isolated by V2_."""

    model_config = SettingsConfigDict(
        env_prefix="V2_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    llm_provider: str = "gemini"
