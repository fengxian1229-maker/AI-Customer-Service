import asyncio
import weakref
from typing import Dict, Iterable, Optional


from langchain_core.language_models.chat_models import BaseChatModel
from cachetools import TTLCache


from .factories.openai import OpenAIModelFactory
from .factories.gemini import GeminiModelFactory
from .factories.base import AbstractModelFactory
from ..utils.logging import setup_logger
from app.core.settings import Settings

settings = Settings()
logger = setup_logger(__name__)


class ModelManager:
    _registry: Dict[str, AbstractModelFactory] = {}
    _instances: TTLCache = TTLCache(
        maxsize=settings.cache.model_instance_cache_maxsize,
        ttl=settings.cache.model_instance_ttl_seconds,
    )
    _locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
    _locks_guard = asyncio.Lock()

    @classmethod
    def register_factory(cls, keys: Iterable[str] | str, factory: AbstractModelFactory) -> None:
        """Register model aliases to a factory; accepts single alias or iterable."""
        alias_list = [keys] if isinstance(keys, str) else list(keys)
        for key in alias_list:
            cls._registry[key.lower()] = factory


    @classmethod
    def _resolve_factory(cls, model_alias: str) -> Optional[AbstractModelFactory]:
        """Exact-match lookup for factory to avoid accidental substring collisions."""
        return cls._registry.get(model_alias.lower())

    @classmethod
    def _normalize_alias(cls, model_alias: str) -> str:
        alias_key = model_alias.lower()
        if alias_key in cls._registry:
            return alias_key
        for alias, name in settings.models.aliases.items():
            if name.lower() == alias_key:
                return alias
        return alias_key

    @classmethod
    def _resolve_gemini_location(cls, model_name: str) -> str:
        normalized_name = str(model_name or "").strip().lower()
        candidate = normalized_name.split("/")[-1]
        if candidate.startswith(("gemini-3", "gemini3")) or "gemini-3" in candidate:
            return settings.cache.gemini3_location
        if candidate.startswith(("gemini-2", "gemini2")) or "gemini-2" in candidate:
            return settings.cache.gemini2_location
        return settings.cache.location

    @classmethod
    async def _get_lock(cls, key: str) -> asyncio.Lock:
        async with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                cls._locks[key] = lock
            return lock


    @classmethod
    async def get_model(
        cls,
        model_alias: str,
        system_prompt: Optional[str] = None,
        force_recreate: bool = False,
        cache_id: Optional[str] = None,
        use_prompt_cache: bool = True,
    ) -> Optional[BaseChatModel]:
        alias_key = cls._normalize_alias(model_alias)
        aliases = settings.models.aliases
        alias_lookup = {key.lower(): key for key in aliases}
        canonical_alias = alias_lookup.get(alias_key)
        if not canonical_alias:
            logger.error(
                "Model alias '%s' is not supported. Expected one of %s.",
                alias_key,
                list(aliases),
            )
            return None
        alias_key = canonical_alias
        resolved_model_name = aliases[alias_key]
        if not resolved_model_name:
            logger.error("Model alias '%s' not found in settings.models.aliases.", alias_key)
            return None

        if resolved_model_name.lower().startswith("gemini"):
            factory = cls._registry.get("gemini")
        else:
            factory = cls._registry.get("openai") or cls._resolve_factory(alias_key)
        if not factory:
            logger.warning("No factory registered for alias '%s'.", alias_key)
            return None

        cache_identifier = None
        cache_name = None
        lock_key = alias_key
        gemini_location = None
        is_gemini_factory = isinstance(factory, GeminiModelFactory)

        if is_gemini_factory:
            gemini_location = cls._resolve_gemini_location(resolved_model_name)
            logger.debug(
                "Resolved Gemini location '%s' for model '%s' (alias='%s', use_prompt_cache=%s).",
                gemini_location,
                resolved_model_name,
                alias_key,
                use_prompt_cache,
            )

        if is_gemini_factory and use_prompt_cache:
            base_cache_identifier = cache_id or factory.cache_display_name
            cache_identifier = (
                f"{gemini_location}:{base_cache_identifier}" if base_cache_identifier else None
            )
            if not cache_identifier:
                logger.error("Gemini cache identifier is required for model '%s'.", alias_key)
                return None
            lock_key = f"{alias_key}:{cache_identifier}"
        elif is_gemini_factory:
            lock_key = f"{alias_key}:{gemini_location}:no_prompt_cache"
        elif use_prompt_cache and cache_id and settings.models.prompt_cache_enabled:
            lock_key = f"{alias_key}:prompt_cache:{cache_id}"

        lock = await cls._get_lock(lock_key)
        async with lock:
            if is_gemini_factory and use_prompt_cache:
                cache_name = await factory.ensure_cache(
                    model_name=resolved_model_name,
                    cache_identifier=cache_identifier,
                    system_prompt=system_prompt,
                    force_recreate=force_recreate,
                    location=gemini_location,
                )

            cache_key = f"{alias_key}:{cache_name}" if cache_name else lock_key

            if force_recreate and cache_key in cls._instances:
                logger.info("Force recreate: evicting cached instance for '%s'.", cache_key)
                del cls._instances[cache_key]

            cached_instance = cls._instances.get(cache_key)
            if cached_instance and not force_recreate:
                if is_gemini_factory and use_prompt_cache:
                    await factory.refresh_cache_ttl(
                        cache_identifier=cache_identifier,
                        location=gemini_location,
                    )
                return cached_instance

            logger.info("Creating new model instance for '%s' (model: %s)...", alias_key, resolved_model_name)

            create_kwargs = {"force_recreate": force_recreate}
            if is_gemini_factory:
                create_kwargs.update(
                    {
                        "cache_identifier": cache_identifier,
                        "cache_name": cache_name,
                        "system_prompt": system_prompt,
                        "use_prompt_cache": use_prompt_cache,
                        "location": gemini_location,
                    }
                )
            elif use_prompt_cache and cache_id:
                create_kwargs["cache_id"] = cache_id

            instance = await factory.create(resolved_model_name, **create_kwargs)

            if instance:
                cls._instances[cache_key] = instance

            return instance

    @classmethod
    async def refresh_gemini_cache_ttl(
        cls,
        cache_id: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        factory = cls._resolve_factory("gemini")
        if isinstance(factory, GeminiModelFactory):
            location = cls._resolve_gemini_location(model_name or "")
            await factory.refresh_cache_ttl(cache_identifier=cache_id, location=location)

gemini_factory = GeminiModelFactory(
    project=settings.cache.project,
    location=settings.cache.location,
    cache_display_name=settings.cache.cache_display_name,
)

openai_factory = OpenAIModelFactory(
    api_key=settings.models.api_key,
    base_url=settings.models.base_url,
)


ModelManager.register_factory(["gemini"], gemini_factory)
ModelManager.register_factory(["openai"], openai_factory)
