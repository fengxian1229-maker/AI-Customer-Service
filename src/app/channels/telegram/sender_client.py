import json
import mimetypes
from pathlib import Path
import socket
import uuid
from typing import Any
from urllib.parse import urlparse
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
    def __init__(
        self,
        bot_token: str,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 15.0,
        attachment_auth_header: str | None = None,
        attachment_download_timeout_seconds: float = 15.0,
        attachment_max_bytes: int = 10 * 1024 * 1024,
        upload_attachments_via_download: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.attachment_auth_header = attachment_auth_header
        self.attachment_download_timeout_seconds = attachment_download_timeout_seconds
        self.attachment_max_bytes = attachment_max_bytes
        self.upload_attachments_via_download = upload_attachments_via_download

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

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "message_thread_id": message_thread_id,
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
        if self.upload_attachments_via_download:
            try:
                attachment = self.download_attachment(photo_url)
                result = self.send_photo_multipart(
                    chat_id=chat_id,
                    file_bytes=attachment["data"],
                    filename=attachment["filename"],
                    content_type=attachment["content_type"],
                    caption=caption,
                    message_thread_id=message_thread_id,
                    reply_to_message_id=reply_to_message_id,
                )
                result["fallback"] = False
                result["upload_mode"] = "multipart"
                return result
            except TelegramApiError as exc:
                if _should_raise_retryable(exc):
                    raise
                return self._send_attachment_fallback(
                    chat_id,
                    photo_url,
                    caption=caption,
                    reason=exc.description,
                    message_thread_id=message_thread_id,
                    reply_to_message_id=reply_to_message_id,
                )
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
            result["upload_mode"] = "url"
            return result
        except TelegramApiError as exc:
            if _should_raise_retryable(exc):
                raise
            return self._send_attachment_fallback(
                chat_id,
                photo_url,
                caption=caption,
                reason=exc.description,
                message_thread_id=message_thread_id,
                reply_to_message_id=reply_to_message_id,
            )

    def download_attachment(self, url: str) -> dict[str, Any]:
        headers = {}
        if self.attachment_auth_header:
            headers["Authorization"] = self.attachment_auth_header
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=self.attachment_download_timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.attachment_max_bytes:
                    raise TelegramApiError("attachment too large", status=413, retryable=False)
                data = response.read(self.attachment_max_bytes + 1)
                if len(data) > self.attachment_max_bytes:
                    raise TelegramApiError("attachment too large", status=413, retryable=False)
                content_type = response.headers.get("Content-Type") or "application/octet-stream"
        except error.HTTPError as exc:
            raise TelegramApiError(_redact_auth(str(exc), self.bot_token, self.attachment_auth_header), status=exc.code, retryable=exc.code >= 500) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise TelegramApiError(_redact_auth(str(exc), self.bot_token, self.attachment_auth_header), retryable=True) from exc
        return {
            "filename": _filename_from_url(url, content_type),
            "content_type": content_type.split(";")[0].strip() or "application/octet-stream",
            "data": data,
        }

    def send_photo_multipart(
        self,
        chat_id: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        caption: str | None = None,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        boundary = f"----codex-telegram-{uuid.uuid4().hex}"
        fields: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": _truncate_caption(caption),
            "message_thread_id": message_thread_id,
            "reply_to_message_id": reply_to_message_id,
        }
        body = bytearray()
        for key, value in fields.items():
            if value is None:
                continue
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(file_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        req = request.Request(
            f"{self.api_base}/bot{self.bot_token}/sendPhoto",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            data = _safe_json(exc.read())
            raise TelegramApiError(
                _redact_auth(data.get("description") or str(exc), self.bot_token, self.attachment_auth_header),
                status=exc.code,
                error_code=data.get("error_code"),
                data=data,
                retryable=exc.code in {429, 500, 502, 503, 504},
            ) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise TelegramApiError(_redact_auth(str(exc), self.bot_token, self.attachment_auth_header), retryable=True) from exc
        if not data.get("ok"):
            raise TelegramApiError(
                _redact_auth(data.get("description") or "Telegram API returned ok=false", self.bot_token, self.attachment_auth_header),
                error_code=data.get("error_code"),
                data=data,
                retryable=data.get("error_code") in {429, 500, 502, 503, 504},
            )
        return data

    def send_document(
        self,
        chat_id: str,
        document_path: str,
        caption: str | None = None,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        path = Path(document_path)
        file_bytes = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.send_document_multipart(
            chat_id=chat_id,
            file_bytes=file_bytes,
            filename=path.name,
            content_type=content_type,
            caption=caption,
            message_thread_id=message_thread_id,
            reply_to_message_id=reply_to_message_id,
        )

    def send_document_multipart(
        self,
        chat_id: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        caption: str | None = None,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        boundary = f"----codex-telegram-{uuid.uuid4().hex}"
        fields: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": caption,
            "message_thread_id": message_thread_id,
            "reply_to_message_id": reply_to_message_id,
        }
        body = bytearray()
        for key, value in fields.items():
            if value is None:
                continue
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(file_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        req = request.Request(
            f"{self.api_base}/bot{self.bot_token}/sendDocument",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            data = _safe_json(exc.read())
            raise TelegramApiError(
                _redact_auth(data.get("description") or str(exc), self.bot_token, self.attachment_auth_header),
                status=exc.code,
                error_code=data.get("error_code"),
                data=data,
                retryable=exc.code in {429, 500, 502, 503, 504},
            ) from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise TelegramApiError(_redact_auth(str(exc), self.bot_token, self.attachment_auth_header), retryable=True) from exc
        if not data.get("ok"):
            raise TelegramApiError(
                _redact_auth(data.get("description") or "Telegram API returned ok=false", self.bot_token, self.attachment_auth_header),
                error_code=data.get("error_code"),
                data=data,
                retryable=data.get("error_code") in {429, 500, 502, 503, 504},
            )
        return data

    def _send_attachment_fallback(
        self,
        chat_id: str,
        photo_url: str,
        caption: str | None,
        reason: str,
        message_thread_id: int | None,
        reply_to_message_id: int | None,
    ) -> dict[str, Any]:
        safe_reason = _redact_auth(reason, self.bot_token, self.attachment_auth_header)
        text = "\n".join(
            part
            for part in [
                "[Attachment fallback]",
                caption,
                photo_url,
                f"Reason: {safe_reason}",
            ]
            if part
        )
        fallback = self.send_message(
            chat_id,
            text,
            message_thread_id=message_thread_id,
            reply_to_message_id=reply_to_message_id,
        )
        fallback["fallback"] = True
        fallback["fallback_reason"] = safe_reason
        return fallback

    def send_case_card(self, card: dict[str, Any]) -> dict[str, Any]:
        main = self.send_message(card["chat_id"], card["card_text"], message_thread_id=card.get("thread_id"))
        message_id = main["result"]["message_id"]
        attachment_results = []
        for attachment in card.get("attachments") or []:
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
        edit_message_id = append.get("edit_message_id") or append.get("reply_to_message_id")
        update = self.edit_message_text(
            append["chat_id"],
            int(edit_message_id),
            append["text"],
            message_thread_id=append.get("thread_id"),
        )
        message_id = update.get("result", {}).get("message_id") or edit_message_id
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
        return {
            "ok": True,
            "status": "edited",
            "message_id": message_id,
            "reply_to_message_id": reply_to,
            "card_text": append["text"],
            "attachment_results": attachment_results,
        }

    def send_case_followup(self, followup: dict[str, Any]) -> dict[str, Any]:
        main = self.send_message(
            followup["chat_id"],
            followup["text"],
            message_thread_id=followup.get("thread_id"),
            reply_to_message_id=int(followup["root_message_id"]),
        )
        message_id = int(main["result"]["message_id"])
        attachment_results = []
        attachment_errors = []
        for attachment in followup.get("attachments") or []:
            try:
                attachment_results.append(
                    self.send_photo_from_url(
                        followup["chat_id"],
                        attachment["url"],
                        caption=attachment.get("name"),
                        message_thread_id=followup.get("thread_id"),
                        reply_to_message_id=message_id,
                    )
                )
            except Exception as exc:
                attachment_errors.append(
                    {"name": attachment.get("name"), "url": attachment.get("url"), "error": str(exc)}
                )
        return {
            "ok": True,
            "message_id": message_id,
            "attachment_results": attachment_results,
            "attachment_errors": attachment_errors,
        }


def _safe_json(raw: bytes) -> dict[str, Any]:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _redact_token(text: str, token: str) -> str:
    return str(text).replace(token, "[REDACTED_TELEGRAM_TOKEN]") if token else str(text)


def _redact_auth(text: str, token: str, auth_header: str | None = None) -> str:
    redacted = _redact_token(text, token)
    if auth_header:
        redacted = redacted.replace(auth_header, "[REDACTED_AUTHORIZATION]")
    return redacted


def _should_raise_retryable(exc: TelegramApiError) -> bool:
    return bool(exc.retryable or exc.status in {429, 500, 502, 503, 504} or exc.error_code in {429, 500, 502, 503, 504})


def _filename_from_url(url: str, content_type: str) -> str:
    path = urlparse(url).path.rstrip("/")
    filename = path.rsplit("/", 1)[-1] if path else ""
    if filename and "." in filename:
        return filename
    extension = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ".jpg"
    return f"attachment{extension}"


def _truncate_caption(caption: str | None) -> str | None:
    if caption is None:
        return None
    return str(caption)[:1024]
