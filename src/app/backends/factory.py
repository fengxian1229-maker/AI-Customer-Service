from app.backends.base import BackendProvider
from app.backends.config import BackendConfig
from app.backends.tac_client import TacBackendClient


class UnsupportedBackendProviderError(ValueError):
    pass


class BackendProviderFactory:
    def create(self, config: BackendConfig) -> BackendProvider:
        provider_type = (config.provider_type or "").strip().lower()
        if provider_type == "tac":
            return TacBackendClient(config)
        raise UnsupportedBackendProviderError(f"unsupported backend provider_type: {config.provider_type}")
