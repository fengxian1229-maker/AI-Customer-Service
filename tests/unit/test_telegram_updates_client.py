from io import BytesIO

from app.channels.telegram import updates_client as updates_client_module
from app.channels.telegram.updates_client import TelegramUpdatesClient


def test_download_file_resolves_file_id_without_exposing_bot_token(monkeypatch):
    client = TelegramUpdatesClient("secret-token")
    calls = []

    def fake_request(method, body, timeout_seconds=None):
        calls.append((method, body))
        return {"ok": True, "result": {"file_path": "photos/receipt.jpg"}}

    class Headers:
        def get_content_type(self):
            return "image/jpeg"

        def get(self, name, default=None):
            return default

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size=-1):
            return BytesIO(b"jpeg-bytes").read(size)

    opened_urls = []

    def fake_urlopen(req, timeout):
        opened_urls.append(req.full_url)
        return Response()

    monkeypatch.setattr(client, "request", fake_request)
    monkeypatch.setattr(updates_client_module.request, "urlopen", fake_urlopen)

    downloaded = client.download_file("photo-large")

    assert calls == [("getFile", {"file_id": "photo-large"})]
    assert opened_urls == ["https://api.telegram.org/file/botsecret-token/photos/receipt.jpg"]
    assert downloaded == {
        "content": b"jpeg-bytes",
        "content_type": "image/jpeg",
        "filename": "receipt.jpg",
    }
