import json
import socket
from typing import Any
from urllib import error, request

from app.channels.telegram.sender_client import TelegramApiError


class TelegramUpdatesClient:
    def __init__(
        self,
        bot_token: str,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, body: dict[str, Any], timeout_seconds: float | None = None) -> dict[str, Any]:
        url = f"{self.api_base}/bot{self.bot_token}/{method}"
        payload = json.dumps({key: value for key, value in body.items() if value is not None}).encode("utf-8")
        req = request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=timeout_seconds or self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            data = _safe_json(exc.read())
            raise TelegramApiError(
                _redact_token(data.get("description") or str(exc), self.bot_token),
                status=exc.code,
                error_code=data.get("error_code"),
                data=data,
                retryable=exc.code in {429, 500, 502, 503, 504},
            ) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise TelegramApiError(_redact_token(str(exc), self.bot_token), retryable=True) from exc
        if not data.get("ok"):
            raise TelegramApiError(
                _redact_token(data.get("description") or "Telegram API returned ok=false", self.bot_token),
                error_code=data.get("error_code"),
                data=data,
                retryable=data.get("error_code") in {429, 500, 502, 503, 504},
            )
        return data

    def get_updates(self, offset: int | None = None, timeout: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        data = self.request(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "limit": limit,
                "allowed_updates": ["message"],
            },
            timeout_seconds=self.timeout_seconds + max(timeout, 0) + 5,
        )
        return list(data.get("result") or [])

    def get_me(self) -> dict[str, Any]:
        return self.request("getMe", {})


def _safe_json(raw: bytes) -> dict[str, Any]:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _redact_token(text: str, token: str) -> str:
    return str(text).replace(token, "[REDACTED_TELEGRAM_TOKEN]") if token else str(text)
