from app.backends.config import BackendConfig
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
    ) -> BackendConfig:
        del tenant_id, channel_type, channel_instance_id
        if not self.settings.backend_query_enabled:
            raise BackendConfigError("FAILED_CONFIG", "backend_query_enabled is false")

        config = BackendConfig(
            provider_type=(self.settings.backend_provider_type or "").strip(),
            base_url=_blank_to_none(self.settings.backend_base_url),
            authorization=_blank_to_none(self.settings.backend_authorization),
            merchant_code=_blank_to_none(self.settings.backend_merchant_code),
            login_operator=_blank_to_none(self.settings.backend_login_operator),
            login_password=_blank_to_none(self.settings.backend_login_password),
            login_merchant=_blank_to_none(self.settings.backend_login_merchant),
            request_timeout_seconds=self.settings.backend_request_timeout_seconds,
            default_lookback_days=self.settings.backend_default_lookback_days,
            fallback_lookback_days=self.settings.backend_fallback_lookback_days,
            source="env_default",
        )
        missing = _missing_required_fields(config)
        if missing:
            raise BackendConfigError("FAILED_CONFIG", f"missing backend config: {', '.join(missing)}")
        return config


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
    if not config.authorization and not (config.login_operator and config.login_password):
        missing.append("backend_authorization or backend_login_operator/backend_login_password")
    return missing
