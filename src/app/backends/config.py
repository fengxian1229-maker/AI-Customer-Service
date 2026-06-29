from dataclasses import dataclass
from typing import Any


SECRET_FIELDS = {"authorization", "login_password"}


@dataclass
class BackendConfig:
    provider_type: str
    base_url: str | None
    authorization: str | None
    merchant_code: str | None
    login_operator: str | None
    login_password: str | None
    login_merchant: str | None
    request_timeout_seconds: float
    default_lookback_days: int
    fallback_lookback_days: int
    source: str

    def __repr__(self) -> str:
        return f"BackendConfig({self.sanitized()!r})"

    def sanitized(self) -> dict[str, Any]:
        data = {
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "authorization": self.authorization,
            "merchant_code": self.merchant_code,
            "login_operator": self.login_operator,
            "login_password": self.login_password,
            "login_merchant": self.login_merchant,
            "request_timeout_seconds": self.request_timeout_seconds,
            "default_lookback_days": self.default_lookback_days,
            "fallback_lookback_days": self.fallback_lookback_days,
            "source": self.source,
        }
        for key in SECRET_FIELDS:
            if data.get(key):
                data[key] = "<redacted>"
        return data
