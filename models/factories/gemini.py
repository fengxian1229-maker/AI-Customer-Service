import asyncio
import re
import os
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict


from google import genai
from google.genai.types import CreateCachedContentConfig, HttpOptions, UpdateCachedContentConfig
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from tenacity import retry, stop_after_attempt, wait_exponential


from .base import AbstractModelFactory
from app.utils.logging import setup_logger
from app.core.settings import Settings
from app.core.redis_client import RedisClientManager
settings = Settings()


logger = setup_logger(__name__)


class GeminiModelFactory(AbstractModelFactory):
    """Gemini factory handling cache lifecycle."""

    _clients_by_location: Dict[str, genai.Client] = {}
    _client_lock = asyncio.Lock()
    _LOCK_RELEASE_SCRIPT = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) "
        "else return 0 end"
    )

    def __init__(self, project: str, location: str, cache_display_name: Optional[str] = None):
        self.project = project
        self.location = location
        self.cache_display_name = cache_display_name or ""
        self._cache_ttl = settings.cache.ttl
        self._cache_refresh_threshold_seconds = settings.cache.cache_refresh_threshold_seconds
        self._cache_lock_ttl_seconds = 60
        self._cache_lock_wait_seconds = 6.0
        self._cache_lock_wait_interval = 0.5
        self._cache_name_by_identifier: Dict[str, str] = {}
        self._cache_name_lock = asyncio.Lock()

    def _resolve_location(self, location: Optional[str]) -> str:
        normalized = str(location or self.location).strip()
        return normalized or self.location

    async def _get_client(self, location: Optional[str] = None) -> genai.Client:
        resolved_location = self._resolve_location(location)
        client = type(self)._clients_by_location.get(resolved_location)
        if client:
            return client

        async with type(self)._client_lock:
            client = type(self)._clients_by_location.get(resolved_location)
            if client is None:
                logger.info("Creating shared genai.Client for Gemini (location=%s).", resolved_location)
                client = genai.Client(
                    vertexai=True,
                    project=self.project,
                    location=resolved_location,
                    http_options=HttpOptions(api_version="v1beta1"),
                )
                type(self)._clients_by_location[resolved_location] = client
        return client

    def _resolve_cache_identifier(self, cache_identifier: Optional[str]) -> str:
        if cache_identifier:
            return cache_identifier
        if self.cache_display_name:
            return self.cache_display_name
        raise ValueError("Gemini cache identifier is required.")

    def _update_cache_ttl(self, client: genai.Client, cache_name: str) -> None:
        client.caches.update(name=cache_name, config=UpdateCachedContentConfig(ttl=self._cache_ttl))

    def _cache_key(self, kind: str, cache_identifier: str) -> str:
        if kind == "mapping":
            return f"qa:gemini_cache:{cache_identifier}"
        if kind == "lock":
            return f"qa:gemini_cache_lock:{cache_identifier}"
        if kind == "refresh":
            return f"qa:gemini_cache_refresh:{cache_identifier}"
        raise ValueError(f"Unsupported cache key kind: {kind}")

    def _get_cache_mapping_ttl_seconds(self) -> int:
        ttl_seconds = self._parse_duration_seconds(self._cache_ttl)
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = 3600
        buffer_seconds = max(int(self._cache_refresh_threshold_seconds), 0)
        return int(ttl_seconds + buffer_seconds)

    async def _get_redis_client(self):
        try:
            return await RedisClientManager.get_client()
        except Exception as exc:
            logger.warning("Redis unavailable for Gemini cache mapping: %s", exc)
            return None

    async def _redis_get_text(self, redis_client, key: str, log_key_type: str) -> Optional[str]:
        try:
            value = await redis_client.get(key)
        except Exception as exc:
            logger.warning("Redis GET failed for Gemini %s key %s: %s", log_key_type, key, exc)
            return None

        if not value:
            return None
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8")
        return str(value)

    async def _redis_set_text(
        self,
        redis_client,
        key: str,
        value: str,
        ttl_seconds: int,
        log_key_type: str,
    ) -> None:
        try:
            await redis_client.set(key, value, ex=ttl_seconds)
        except Exception as exc:
            logger.warning("Redis SET failed for Gemini %s key %s: %s", log_key_type, key, exc)

    async def _redis_delete_key(self, redis_client, key: str, log_key_type: str) -> None:
        try:
            await redis_client.delete(key)
        except Exception as exc:
            logger.warning("Redis DEL failed for Gemini %s key %s: %s", log_key_type, key, exc)

    async def _redis_get_cache_name(self, redis_client, cache_identifier: str) -> Optional[str]:
        key = self._cache_key("mapping", cache_identifier)
        return await self._redis_get_text(redis_client, key, "cache")

    async def _redis_set_cache_name(self, redis_client, cache_identifier: str, cache_name: str) -> None:
        key = self._cache_key("mapping", cache_identifier)
        ttl_seconds = self._get_cache_mapping_ttl_seconds()
        await self._redis_set_text(redis_client, key, cache_name, ttl_seconds, "cache")

    async def _redis_get_cache_refresh_at(self, redis_client, cache_identifier: str) -> Optional[float]:
        key = self._cache_key("refresh", cache_identifier)
        value = await self._redis_get_text(redis_client, key, "cache refresh")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _redis_set_cache_refresh_at(
        self, redis_client, cache_identifier: str, timestamp: float
    ) -> None:
        key = self._cache_key("refresh", cache_identifier)
        ttl_seconds = self._get_cache_mapping_ttl_seconds()
        await self._redis_set_text(redis_client, key, f"{timestamp}", ttl_seconds, "cache refresh")

    async def _redis_delete_cache_name(self, redis_client, cache_identifier: str) -> None:
        key = self._cache_key("mapping", cache_identifier)
        await self._redis_delete_key(redis_client, key, "cache")

    async def _acquire_cache_lock(self, redis_client, cache_identifier: str) -> Optional[str]:
        lock_key = self._cache_key("lock", cache_identifier)
        token = uuid.uuid4().hex
        try:
            acquired = await redis_client.set(
                lock_key,
                token,
                nx=True,
                ex=self._cache_lock_ttl_seconds,
            )
        except Exception as exc:
            logger.warning("Redis lock acquisition failed for %s: %s", lock_key, exc)
            return None

        return token if acquired else None

    async def _release_cache_lock(self, redis_client, cache_identifier: str, token: str) -> None:
        lock_key = self._cache_key("lock", cache_identifier)

        for attempt in range(3):
            try:
                await redis_client.eval(self._LOCK_RELEASE_SCRIPT, 1, lock_key, token)
                return
            except Exception as exc:
                if attempt == 2:
                    logger.warning("Redis lock release failed for %s: %s", lock_key, exc)
                else:
                    logger.warning(
                        "Redis lock release failed for %s (attempt %s/3): %s",
                        lock_key,
                        attempt + 1,
                        exc,
                    )
                    await asyncio.sleep(0.2 * (attempt + 1))

    async def _wait_for_cache_mapping(self, redis_client, cache_identifier: str) -> Optional[str]:
        deadline = time.monotonic() + self._cache_lock_wait_seconds
        while time.monotonic() < deadline:
            cache_name = await self._redis_get_cache_name(redis_client, cache_identifier)
            if cache_name:
                return cache_name
            await asyncio.sleep(self._cache_lock_wait_interval)
        return None

    async def _validate_redis_cache_name(
        self,
        client: genai.Client,
        redis_client,
        cache_identifier: str,
        cache_name: str,
    ) -> bool:
        cache_info = self._get_cache_info(client, cache_name)
        if cache_info is None:
            logger.warning(
                "Redis mapping for %s points to missing cache %s; evicting.",
                cache_identifier,
                cache_name,
            )
            await self._redis_delete_cache_name(redis_client, cache_identifier)
            await self._forget_cache_name(cache_identifier)
            return False
        if self._is_cache_expired(cache_info):
            logger.warning(
                "Redis mapping for %s points to expired cache %s; evicting.",
                cache_identifier,
                cache_name,
            )
            await self._redis_delete_cache_name(redis_client, cache_identifier)
            await self._forget_cache_name(cache_identifier)
            return False
        await self._remember_cache_name(cache_identifier, cache_name)
        try:
            refreshed = await self._refresh_cache_ttl_if_needed(
                client, redis_client, cache_info, cache_identifier
            )
            if refreshed:
                await self._redis_set_cache_name(redis_client, cache_identifier, cache_name)
        except Exception as exc:
            logger.warning(
                "Failed to refresh Redis cached name %s: %s; evicting mapping.",
                cache_name,
                exc,
            )
            await self._redis_delete_cache_name(redis_client, cache_identifier)
            await self._forget_cache_name(cache_identifier)
            return False
        return True

    def _parse_duration_seconds(self, value: object) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, timedelta):
            return value.total_seconds()
        if hasattr(value, "seconds") and hasattr(value, "nanos"):
            try:
                return float(value.seconds) + float(value.nanos) / 1_000_000_000
            except Exception:
                return None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return float(text)
            if text.endswith("s") and text[:-1].isdigit():
                return float(text[:-1])
            match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", text)
            if match:
                hours = int(match.group(1) or 0)
                minutes = int(match.group(2) or 0)
                seconds = int(match.group(3) or 0)
                return float(hours * 3600 + minutes * 60 + seconds)
        return None

    def _to_datetime(self, value: object) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if hasattr(value, "ToDatetime"):
            try:
                dt = value.ToDatetime()
            except Exception:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if hasattr(value, "seconds") and hasattr(value, "nanos"):
            try:
                return datetime.fromtimestamp(
                    value.seconds + value.nanos / 1_000_000_000, tz=timezone.utc
                )
            except Exception:
                return None
        if isinstance(value, str):
            text = value.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return None

    def _get_cache_expire_time(self, cache: object) -> Optional[datetime]:
        for attr in ("expire_time", "expireTime", "expiration_time", "expirationTime"):
            dt = self._to_datetime(getattr(cache, attr, None))
            if dt:
                return dt
        return None

    def _get_cache_create_time(self, cache: object) -> Optional[datetime]:
        for attr in ("create_time", "createTime", "creation_time", "creationTime"):
            dt = self._to_datetime(getattr(cache, attr, None))
            if dt:
                return dt
        return None

    def _get_cache_remaining_seconds(self, cache: object) -> Optional[float]:
        expire_time = self._get_cache_expire_time(cache)
        now = datetime.now(timezone.utc)
        if expire_time:
            return (expire_time - now).total_seconds()
        ttl_value = getattr(cache, "ttl", None)
        if ttl_value is None:
            config = getattr(cache, "config", None)
            ttl_value = getattr(config, "ttl", None) if config is not None else None
        ttl_seconds = self._parse_duration_seconds(ttl_value)
        if ttl_seconds is not None:
            create_time = self._get_cache_create_time(cache)
            if create_time:
                return (create_time + timedelta(seconds=ttl_seconds) - now).total_seconds()
        return None

    def _is_cache_expired(self, cache: object) -> bool:
        remaining = self._get_cache_remaining_seconds(cache)
        return remaining is not None and remaining <= 0

    async def _should_refresh_by_throttle(self, redis_client, cache_identifier: str) -> bool:
        threshold = max(int(self._cache_refresh_threshold_seconds), 0)
        if threshold == 0:
            return True
        if not redis_client:
            return False
        last_refresh = await self._redis_get_cache_refresh_at(redis_client, cache_identifier)
        if last_refresh is None:
            return True
        return (time.time() - last_refresh) >= threshold

    async def _mark_cache_refreshed(self, redis_client, cache_identifier: str) -> None:
        if not redis_client:
            return
        await self._redis_set_cache_refresh_at(redis_client, cache_identifier, time.time())

    async def _should_refresh_cache(self, redis_client, cache: object, cache_identifier: str) -> bool:
        threshold = max(int(self._cache_refresh_threshold_seconds), 0)
        if threshold == 0:
            return True
        remaining = self._get_cache_remaining_seconds(cache)
        if remaining is None:
            return await self._should_refresh_by_throttle(redis_client, cache_identifier)
        return remaining <= threshold

    async def _refresh_cache_ttl_if_needed(
        self, client: genai.Client, redis_client, cache: object, cache_identifier: str
    ) -> bool:
        if not redis_client:
            logger.debug("Redis unavailable; skip cache TTL refresh for %s.", cache_identifier)
            return False
        if not await self._should_refresh_cache(redis_client, cache, cache_identifier):
            return False
        cache_name = getattr(cache, "name", None)
        if not cache_name:
            raise ValueError("Gemini cache name is required for refresh.")
        self._update_cache_ttl(client, cache_name)
        await self._mark_cache_refreshed(redis_client, cache_identifier)
        return True

    def _get_cache_info(self, client: genai.Client, cache_name: str) -> Optional[object]:
        try:
            return client.caches.get(name=cache_name)
        except Exception as exc:
            logger.warning("Failed to fetch cache %s for TTL inspection: %s", cache_name, exc)
            return None

    async def _refresh_cache_ttl_if_needed_by_name(
        self, client: genai.Client, redis_client, cache_name: str, cache_identifier: str
    ) -> bool:
        if not redis_client:
            logger.debug("Redis unavailable; skip cache TTL refresh for %s.", cache_identifier)
            return False
        cache_info = self._get_cache_info(client, cache_name)
        if cache_info is None:
            if await self._should_refresh_by_throttle(redis_client, cache_identifier):
                self._update_cache_ttl(client, cache_name)
                await self._mark_cache_refreshed(redis_client, cache_identifier)
                return True
            return False
        return await self._refresh_cache_ttl_if_needed(
            client, redis_client, cache_info, cache_identifier
        )

    async def _get_cached_name(self, cache_identifier: str) -> Optional[str]:
        async with self._cache_name_lock:
            return self._cache_name_by_identifier.get(cache_identifier)

    async def _remember_cache_name(self, cache_identifier: str, cache_name: str) -> None:
        async with self._cache_name_lock:
            self._cache_name_by_identifier[cache_identifier] = cache_name

    async def _forget_cache_name(self, cache_identifier: str) -> None:
        async with self._cache_name_lock:
            self._cache_name_by_identifier.pop(cache_identifier, None)

    async def _store_cache_name(self, redis_client, cache_identifier: str, cache_name: str) -> None:
        await self._remember_cache_name(cache_identifier, cache_name)
        if redis_client:
            await self._redis_set_cache_name(redis_client, cache_identifier, cache_name)

    def _matches_cache(self, cache: object, cache_identifier: str) -> bool:
        cache_name = getattr(cache, "name", "")
        return (
            getattr(cache, "display_name", None) == cache_identifier
            or cache_name == cache_identifier
            or cache_name.split("/")[-1] == cache_identifier
        )

    async def _find_existing_cache(
        self, client: genai.Client, cache_identifier: str, redis_client
    ) -> Optional[str]:
        try:
            for cache in client.caches.list():
                if self._matches_cache(cache, cache_identifier):
                    try:
                        refreshed = await self._refresh_cache_ttl_if_needed(
                            client, redis_client, cache, cache_identifier
                        )
                        if refreshed:
                            logger.info("Refreshed TTL for cache %s.", cache.name)
                        else:
                            logger.debug("Skipped TTL refresh for cache %s.", cache.name)
                        return cache.name
                    except Exception as exc:
                        logger.warning("Failed to refresh cache TTL, recreating. Reason: %s", exc)
                        return None
        except Exception as exc:
            logger.warning("Listing caches failed, will recreate. Reason: %s", exc)
        return None

    def _create_cache(self, client: genai.Client, model_name: str, cache_display_name: str, system_prompt: str) -> str:
        cache = client.caches.create(
            model=model_name,
            config=CreateCachedContentConfig(
                contents="",
                system_instruction=system_prompt,
                display_name=cache_display_name,
                ttl=self._cache_ttl,
            ),
        )
        logger.info("Created Gemini cache %s.", cache.name)
        return cache.name

    def _extract_cache_id(self, cache_name: str) -> str:
        return cache_name.split("/")[-1]

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=60))
    async def ensure_cache(
        self,
        model_name: str,
        system_prompt: str,
        cache_identifier: Optional[str] = None,
        force_recreate: bool = False,
        location: Optional[str] = None,
    ) -> str:
        cache_identifier = self._resolve_cache_identifier(cache_identifier)
        client = await self._get_client(location)
        redis_client = await self._get_redis_client()

        if not force_recreate:
            cached_name = await self._get_cached_name(cache_identifier)
            if cached_name:
                try:
                    refreshed = await self._refresh_cache_ttl_if_needed_by_name(
                        client, redis_client, cached_name, cache_identifier
                    )
                    cache_exists = refreshed
                    if not cache_exists:
                        cache_info = self._get_cache_info(client, cached_name)
                        cache_exists = cache_info is not None and not self._is_cache_expired(cache_info)
                    if cache_exists:
                        if refreshed and redis_client:
                            await self._redis_set_cache_name(redis_client, cache_identifier, cached_name)
                        logger.info("Using cached Gemini content %s.", cached_name)
                        return cached_name
                    logger.warning(
                        "In-memory mapping for %s points to missing/expired cache %s; evicting.",
                        cache_identifier,
                        cached_name,
                    )
                    await self._forget_cache_name(cache_identifier)
                except Exception as exc:
                    logger.warning("Failed to refresh cached name %s: %s", cached_name, exc)
                    await self._forget_cache_name(cache_identifier)

        if not force_recreate and redis_client:
            redis_name = await self._redis_get_cache_name(redis_client, cache_identifier)
            if redis_name:
                if await self._validate_redis_cache_name(
                    client, redis_client, cache_identifier, redis_name
                ):
                    logger.info("Using Redis-mapped Gemini content %s.", redis_name)
                    return redis_name

        if not force_recreate:
            cache_name = await self._find_existing_cache(client, cache_identifier, redis_client)
            if cache_name:
                await self._store_cache_name(redis_client, cache_identifier, cache_name)
                return cache_name

        lock_token = None
        if redis_client:
            max_wait_seconds = 120.0
            wait_deadline = time.monotonic() + max_wait_seconds
            while True:
                lock_token = await self._acquire_cache_lock(redis_client, cache_identifier)
                if lock_token is not None:
                    break
                waited_name = await self._wait_for_cache_mapping(redis_client, cache_identifier)
                if waited_name:
                    if await self._validate_redis_cache_name(
                        client, redis_client, cache_identifier, waited_name
                    ):
                        logger.info(
                            "Using Redis-mapped Gemini content %s after wait.",
                            waited_name,
                        )
                        return waited_name
                if time.monotonic() >= wait_deadline:
                    logger.warning(
                        "Redis cache lock wait exceeded %.0f seconds for %s; proceeding without lock.",
                        max_wait_seconds,
                        cache_identifier,
                    )
                    break

        try:
            if redis_client and lock_token and not force_recreate:
                redis_name = await self._redis_get_cache_name(redis_client, cache_identifier)
                if redis_name:
                    if await self._validate_redis_cache_name(
                        client, redis_client, cache_identifier, redis_name
                    ):
                        return redis_name

            if not force_recreate:
                cache_name = await self._find_existing_cache(client, cache_identifier, redis_client)
                if cache_name:
                    await self._store_cache_name(redis_client, cache_identifier, cache_name)
                    return cache_name

            cache_name = self._create_cache(
                client,
                model_name=model_name,
                cache_display_name=cache_identifier,
                system_prompt=system_prompt,
            )
            await self._store_cache_name(redis_client, cache_identifier, cache_name)
            return cache_name
        finally:
            if redis_client and lock_token:
                await self._release_cache_lock(redis_client, cache_identifier, lock_token)

    async def create(
        self,
        model_name: str,
        system_prompt: Optional[str] = None,
        force_recreate: bool = False,
        cache_identifier: Optional[str] = None,
        cache_name: Optional[str] = None,
        use_prompt_cache: bool = True,
        location: Optional[str] = None,
        **kwargs,
    ) -> Optional[BaseChatModel]:
        resolved_location = self._resolve_location(location)
        try:
            if use_prompt_cache:
                if not cache_name:
                    cache_name = await self.ensure_cache(
                        model_name=model_name,
                        cache_identifier=cache_identifier,
                        force_recreate=force_recreate,
                        system_prompt=system_prompt,
                        location=resolved_location,
                    )
                cache_id = self._extract_cache_id(cache_name)
                return ChatGoogleGenerativeAI(
                    project=self.project,
                    model=model_name,
                    cached_content=cache_id,
                    # temperature=0,
                    location=resolved_location,
                    # thinking_level="high"
                )

            logger.info(
                "Creating Gemini model without prompt cache (model=%s, location=%s).",
                model_name,
                resolved_location,
            )
            return ChatGoogleGenerativeAI(
                project=self.project,
                model=model_name,
                # temperature=0,
                location=resolved_location,
                # thinking_level="high"
            )
        except Exception as exc:
            logger.error("Failed to create Gemini model: %s", exc, exc_info=True)
            raise

    async def refresh_cache_ttl(
        self,
        cache_identifier: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        client = await self._get_client(location)
        try:
            cache_identifier = self._resolve_cache_identifier(cache_identifier)
            redis_client = await self._get_redis_client()
            if not redis_client:
                logger.info("Redis unavailable; skip cache TTL refresh for %s.", cache_identifier)
                return
            cached_name = await self._get_cached_name(cache_identifier)
            if cached_name:
                try:
                    refreshed = await self._refresh_cache_ttl_if_needed_by_name(
                        client, redis_client, cached_name, cache_identifier
                    )
                    if refreshed:
                        logger.info("Heartbeat refreshed TTL for cache %s.", cached_name)
                        await self._redis_set_cache_name(redis_client, cache_identifier, cached_name)
                        return

                    cache_info = self._get_cache_info(client, cached_name)
                    if cache_info is not None and not self._is_cache_expired(cache_info):
                        logger.debug("Heartbeat skipped TTL refresh for cache %s.", cached_name)
                        return

                    logger.warning(
                        "In-memory mapping for %s points to missing/expired cache %s; evicting.",
                        cache_identifier,
                        cached_name,
                    )
                    await self._forget_cache_name(cache_identifier)
                except Exception as exc:
                    logger.warning("Failed to refresh cached name %s: %s", cached_name, exc)
                    await self._forget_cache_name(cache_identifier)
            for cache in client.caches.list():
                if self._matches_cache(cache, cache_identifier):
                    refreshed = await self._refresh_cache_ttl_if_needed(
                        client, redis_client, cache, cache_identifier
                    )
                    if refreshed:
                        logger.info("Heartbeat refreshed TTL for cache %s.", cache.name)
                        await self._redis_set_cache_name(redis_client, cache_identifier, cache.name)
                    else:
                        logger.debug("Heartbeat skipped TTL refresh for cache %s.", cache.name)
                    await self._store_cache_name(redis_client, cache_identifier, cache.name)
                    break
            else:
                logger.warning("No Gemini cache found to refresh; it will be recreated on next request.")
        except Exception as exc:
            logger.error("Failed to refresh Gemini cache TTL: %s", exc, exc_info=True)
