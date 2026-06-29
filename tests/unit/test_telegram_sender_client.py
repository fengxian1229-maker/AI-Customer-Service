import json
from urllib import error

import pytest

from app.channels.telegram.sender_client import TelegramApiError, TelegramSenderClient


def test_send_case_card_sends_main_card_then_attachment_reply_to_main(monkeypatch):
    calls = []
    client = TelegramSenderClient("secret", upload_attachments_via_download=False)

    def fake_request(method, body, timeout_seconds=None):
        calls.append((method, body))
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 100}}
        return {"ok": True, "result": {"message_id": 101}}

    monkeypatch.setattr(client, "request", fake_request)

    result = client.send_case_card(
        {
            "chat_id": "-100test",
            "thread_id": None,
            "card_text": "card",
            "attachments": [{"url": "https://cdn.example/a.png", "name": "screenshot"}],
        }
    )

    assert result["message_id"] == 100
    assert calls[0] == ("sendMessage", {"chat_id": "-100test", "text": "card", "message_thread_id": None, "reply_to_message_id": None, "disable_web_page_preview": True})
    assert calls[1][0] == "sendPhoto"
    assert calls[1][1]["reply_to_message_id"] == 100


def test_send_photo_from_url_downloads_private_attachment_and_uploads_multipart(monkeypatch):
    client = TelegramSenderClient("secret", attachment_auth_header="Basic abc", upload_attachments_via_download=True)
    calls = []

    monkeypatch.setattr(
        client,
        "download_attachment",
        lambda url: {"filename": "a.png", "content_type": "image/png", "data": b"img"},
    )

    def fake_multipart(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "result": {"message_id": 10}}

    monkeypatch.setattr(client, "send_photo_multipart", fake_multipart)

    result = client.send_photo_from_url("-100test", "https://files.livechatinc.com/a.png", reply_to_message_id=123)

    assert result["upload_mode"] == "multipart"
    assert calls[0]["reply_to_message_id"] == 123
    assert calls[0]["file_bytes"] == b"img"


def test_download_attachment_sends_authorization_header(monkeypatch):
    seen = {}
    client = TelegramSenderClient("secret", attachment_auth_header="Basic abc", attachment_max_bytes=10)

    class Response:
        headers = {"Content-Type": "image/png", "Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size=-1):
            return b"abc"

    def fake_urlopen(req, timeout):
        seen["authorization"] = req.headers.get("Authorization")
        return Response()

    monkeypatch.setattr("app.channels.telegram.sender_client.request.urlopen", fake_urlopen)

    downloaded = client.download_attachment("https://files.livechatinc.com/path/a.png")

    assert seen["authorization"] == "Basic abc"
    assert downloaded["filename"] == "a.png"
    assert downloaded["data"] == b"abc"


def test_multipart_body_contains_chat_reply_and_photo(monkeypatch):
    captured = {}
    client = TelegramSenderClient("secret")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 12}}).encode()

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        captured["content_type"] = req.headers["Content-type"]
        return Response()

    monkeypatch.setattr("app.channels.telegram.sender_client.request.urlopen", fake_urlopen)

    client.send_photo_multipart(
        chat_id="-100test",
        file_bytes=b"img",
        filename="a.png",
        content_type="image/png",
        reply_to_message_id=123,
    )

    body = captured["body"]
    assert b'name="chat_id"' in body
    assert b"-100test" in body
    assert b'name="reply_to_message_id"' in body
    assert b"123" in body
    assert b'name="photo"; filename="a.png"' in body


def test_download_too_large_falls_back_to_text_url(monkeypatch):
    client = TelegramSenderClient("secret", upload_attachments_via_download=True)
    sent = []

    def too_large(url):
        raise TelegramApiError("attachment too large", status=413, retryable=False)

    monkeypatch.setattr(client, "download_attachment", too_large)
    monkeypatch.setattr(client, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "result": {"message_id": 9}})

    result = client.send_photo_from_url("-100test", "https://cdn.example/a.png", caption="cap", reply_to_message_id=123)

    assert result["fallback"] is True
    assert "[Attachment fallback]" in sent[0][0][1]
    assert "https://cdn.example/a.png" in sent[0][0][1]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_telegram_errors_do_not_fallback(monkeypatch, status):
    client = TelegramSenderClient("secret", upload_attachments_via_download=False)
    monkeypatch.setattr(
        client,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(TelegramApiError("retry later", status=status, retryable=True)),
    )

    with pytest.raises(TelegramApiError) as exc:
        client.send_photo_from_url("-100test", "https://cdn.example/a.png")

    assert exc.value.retryable is True


def test_send_photo_invalid_url_falls_back(monkeypatch):
    client = TelegramSenderClient("secret", upload_attachments_via_download=False)
    calls = []

    def fake_request(method, body, timeout_seconds=None):
        calls.append((method, body))
        if method == "sendPhoto":
            raise TelegramApiError("wrong file identifier/HTTP URL specified", status=400, retryable=False)
        return {"ok": True, "result": {"message_id": 22}}

    monkeypatch.setattr(client, "request", fake_request)

    result = client.send_photo_from_url("-100test", "https://cdn.example/a.png")

    assert result["fallback"] is True
    assert calls[-1][0] == "sendMessage"


def test_request_redacts_token_from_http_errors(monkeypatch):
    token = "secret-token"
    client = TelegramSenderClient(token)

    class FakeHttpError(error.HTTPError):
        def read(self):
            return json.dumps({"ok": False, "description": f"bad {token}", "error_code": 400}).encode()

    def fake_urlopen(req, timeout):
        raise FakeHttpError(req.full_url, 400, "bad", {}, None)

    monkeypatch.setattr("app.channels.telegram.sender_client.request.urlopen", fake_urlopen)

    with pytest.raises(TelegramApiError) as exc:
        client.request("sendMessage", {"chat_id": "x", "text": "x"})

    assert token not in str(exc.value)
