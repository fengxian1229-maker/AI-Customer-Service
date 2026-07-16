from typing import Any

from app.backends.config import BackendConfig
from app.config.platforms import (
    merchant_for_livechat_group_id,
    normalize_livechat_group_id,
    normalize_platform,
    platform_for_livechat_group_id,
)
from app.core.settings import Settings


class BackendConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TenantBackendConfigResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(
        self,
        tenant_id: str | None,
        channel_type: str | None = None,
        channel_instance_id: str | None = None,
        livechat_group_id: Any = None,
        platform: Any = None,
    ) -> BackendConfig:
        del tenant_id, channel_instance_id
        if not self.settings.backend_query_enabled:
            raise BackendConfigError("FAILED_CONFIG", "backend_query_enabled is false")

        merchant_code = _blank_to_none(self.settings.backend_merchant_code)
        source = "env_default"
        resolved_group_id = None
        resolved_platform = None
        if str(channel_type or "").strip().lower() == "livechat":
            if livechat_group_id is None or str(livechat_group_id).strip() == "":
                raise _routing_error("livechat_group_id is required", livechat_group_id, platform)
            group_id = normalize_livechat_group_id(livechat_group_id)
            if group_id is None:
                raise _routing_error(
                    "livechat_group_id must be a positive integer",
                    livechat_group_id,
                    platform,
                )
            expected_platform = platform_for_livechat_group_id(group_id)
            merchant_code = merchant_for_livechat_group_id(group_id)
            if expected_platform is None or merchant_code is None:
                raise _routing_error(
                    f"livechat_group_id {group_id} has no backend merchant mapping",
                    livechat_group_id,
                    platform,
                )
            supplied_platform = normalize_platform(platform)
            if supplied_platform and supplied_platform != expected_platform:
                raise _routing_error(
                    f"platform {supplied_platform} does not match livechat_group_id {group_id} ({expected_platform})",
                    livechat_group_id,
                    platform,
                )
            source = f"livechat_group:{group_id}"
            resolved_group_id = group_id
            resolved_platform = expected_platform

        config = BackendConfig(
            provider_type=(self.settings.backend_provider_type or "").strip(),
            base_url=_blank_to_none(self.settings.backend_base_url),
            authorization=_blank_to_none(self.settings.backend_authorization),
            merchant_code=merchant_code,
            login_operator=_blank_to_none(self.settings.backend_login_operator),
            login_password=_blank_to_none(self.settings.backend_login_password),
            totp_secret=_blank_to_none(self.settings.backend_totp_secret),
            login_merchant=_blank_to_none(self.settings.backend_login_merchant),
            request_timeout_seconds=self.settings.backend_request_timeout_seconds,
            default_lookback_days=self.settings.backend_default_lookback_days,
            fallback_lookback_days=self.settings.backend_fallback_lookback_days,
            source=source,
            livechat_group_id=resolved_group_id,
            platform=resolved_platform,
        )
        missing = _missing_required_fields(config)
        if missing:
            raise BackendConfigError("FAILED_CONFIG", f"missing backend config: {', '.join(missing)}")
        return config


def _routing_error(reason: str, group_id: Any, platform: Any) -> BackendConfigError:
    normalized_platform = normalize_platform(platform) or None
    return BackendConfigError(
        "FAILED_CONFIG",
        f"{reason}; livechat_group_id={group_id!r}, platform={normalized_platform!r}",
    )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _missing_required_fields(config: BackendConfig) -> list[str]:
    missing = []
    for field_name in ("provider_type", "base_url", "merchant_code"):
        if not getattr(config, field_name):
            missing.append(f"backend_{field_name}")
    if not config.authorization and not (config.login_operator and (config.login_password or config.totp_secret)):
        missing.append("backend_authorization or backend_login_operator/backend_login_password or backend_login_operator/backend_totp_secret")
    return missing
