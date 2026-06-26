import os
import platform
from typing import Optional


from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI


from .base import AbstractModelFactory
from app.core.settings import Settings
from app.utils.logging import setup_logger

settings = Settings()

logger = setup_logger(
    __name__
)  # Log failures while building OpenAI-compatible clients.


def _maybe_patch_openai_platform() -> None:
    """Avoid OpenAI platform detection spawning subprocesses in threaded gRPC servers."""
    enabled = os.getenv("QA_OPENAI_SAFE_PLATFORM", "1").lower() in {"1", "true", "yes"}
    if not enabled:
        return
    try:
        import openai._base_client as base_client
    except Exception:
        return

    def _safe_get_platform() -> str:
        try:
            system = platform.system().lower()
        except Exception:
            return "Unknown"
        if system == "darwin":
            return "MacOS"
        if system == "windows":
            return "Windows"
        if system == "linux":
            return "Linux"
        return "Unknown"

    base_client.get_platform = _safe_get_platform


class OpenAIModelFactory(AbstractModelFactory):
    """Factory for GPT/Claude style models served via OpenAI-compatible APIs."""

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url

    async def create(
        self, model_name: str, force_recreate: bool = False, **kwargs
    ) -> Optional[BaseChatModel]:
        try:
            _maybe_patch_openai_platform()
            normalized_model_name = str(model_name or "").strip().lower()
            is_gpt5 = normalized_model_name == "gpt-5"
            timeout_seconds = float(settings.models.openai_timeout_seconds)
            if is_gpt5:
                # Keep GPT-5 request timeout at least 2 minutes for long responses.
                timeout_seconds = max(timeout_seconds, 120.0)
            max_retries = max(int(settings.models.openai_max_retries), 0)
            if is_gpt5:
                # For GPT-5 retry path, avoid hidden provider-level retries.
                max_retries = 0
            model_kwargs = {}
            prompt_cache_key = str(kwargs.get("cache_id") or "").strip()
            if settings.models.prompt_cache_enabled and prompt_cache_key:
                model_kwargs["prompt_cache_key"] = prompt_cache_key
                prompt_cache_retention = str(
                    settings.models.prompt_cache_retention or ""
                ).strip()
                if prompt_cache_retention:
                    model_kwargs["prompt_cache_retention"] = prompt_cache_retention
            return ChatOpenAI(
                model=model_name,
                temperature=0,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
                model_kwargs=model_kwargs,
            )
        except Exception as exc:
            logger.error(
                "Failed to create OpenAI-compatible model: %s", exc, exc_info=True
            )
            raise
