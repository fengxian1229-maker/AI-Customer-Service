import json
import socket
from typing import Any
from urllib import error, request


class TelegramApiError(Exception):
    def __init__(
        self,
        description: str,
        status: int | None = None,
        error_code: int | None = None,
        data: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(description)
        self.status = status
        self.error_code = error_code
        self.description = description
        self.data = data
        self.retryable = retryable


class TelegramSenderClient:
    def __init__(self, bot_token: str, api_base: str = "https://api.telegram.org", timeout_seconds: float = 15.0) -> None:
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

    def send_message(
        self,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "message_thread_id": message_thread_id,
                "reply_to_message_id": reply_to_message_id,
                "disable_web_page_preview": True,
            },
        )

    def send_photo_from_url(
        self,
        chat_id: str,
        photo_url: str,
        caption: str | None = None,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            result = self.request(
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": caption,
                    "message_thread_id": message_thread_id,
                    "reply_to_message_id": reply_to_message_id,
                },
            )
            result["fallback"] = False
            return result
        except TelegramApiError as exc:
            fallback = self.send_message(
                chat_id,
                "\n".join(part for part in [caption, photo_url] if part),
                message_thread_id=message_thread_id,
                reply_to_message_id=reply_to_message_id,
            )
            fallback["fallback"] = True
            fallback["fallback_reason"] = exc.description
            return fallback

    def send_case_card(self, card: dict[str, Any]) -> dict[str, Any]:
        main = self.send_message(card["chat_id"], card["card_text"], message_thread_id=card.get("thread_id"))
        message_id = main["result"]["message_id"]
        attachment_results = []
        for attachment in card.get("attachments") or []:
            # TODO(P9-A.1): support authenticated LiveChat download plus multipart upload.
            attachment_results.append(
                self.send_photo_from_url(
                    card["chat_id"],
                    attachment["url"],
                    caption=attachment.get("name"),
                    message_thread_id=card.get("thread_id"),
                    reply_to_message_id=message_id,
                )
            )
        return {"ok": True, "message_id": message_id, "attachment_results": attachment_results}

    def append_to_case(self, append: dict[str, Any]) -> dict[str, Any]:
        update = self.send_message(
            append["chat_id"],
            append["text"],
            message_thread_id=append.get("thread_id"),
            reply_to_message_id=append.get("reply_to_message_id"),
        )
        message_id = update["result"]["message_id"]
        reply_to = append.get("reply_to_message_id") or message_id
        attachment_results = []
        for attachment in append.get("attachments") or []:
            attachment_results.append(
                self.send_photo_from_url(
                    append["chat_id"],
                    attachment["url"],
                    caption=attachment.get("name"),
                    message_thread_id=append.get("thread_id"),
                    reply_to_message_id=reply_to,
                )
            )
        return {"ok": True, "message_id": message_id, "reply_to_message_id": reply_to, "attachment_results": attachment_results}


def _safe_json(raw: bytes) -> dict[str, Any]:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _redact_token(text: str, token: str) -> str:
    return str(text).replace(token, "[REDACTED_TELEGRAM_TOKEN]") if token else str(text)
