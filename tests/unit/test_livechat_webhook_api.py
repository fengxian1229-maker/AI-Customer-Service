from fastapi.testclient import TestClient

from app.api.app import build_app
from app.core.settings import Settings


class FakeRepository:
    def __init__(self, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.events = []

    async def insert(self, event):
        self.events.append(event)
        return {"inserted": not self.duplicate, "duplicate": self.duplicate}


def make_settings(**overrides):
    values = {
        "livechat_agent_access_token": "token",
        "livechat_account_id": "account",
        "livechat_webhook_secret": "secret",
    }
    values.update(overrides)
    return Settings(**values)


def body(secret="secret"):
    return {
        "webhook_id": "webhook-1",
        "secret_key": secret,
        "action": "incoming_event",
        "organization_id": "org-1",
        "payload": {
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "access": {"group_ids": [23]},
            "event": {
                "id": "event-1",
                "type": "message",
                "author_id": "customer-1",
                "created_at": "2026-07-06T00:00:00Z",
                "text": "hello",
            },
        },
    }


def test_endpoint_inserts_and_returns_counts():
    repository = FakeRepository()
    app = build_app(settings=make_settings(), repository=repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", json=body())

    assert response.status_code == 202
    assert response.json() == {
        "ok": True,
        "action": "incoming_event",
        "normalized": 1,
        "inserted": 1,
        "duplicates": 0,
        "ignored": 0,
    }
    assert len(repository.events) == 1


def test_endpoint_reports_duplicate_insert():
    repository = FakeRepository(duplicate=True)
    app = build_app(settings=make_settings(), repository=repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", json=body())

    assert response.status_code == 202
    assert response.json()["inserted"] == 0
    assert response.json()["duplicates"] == 1


def test_endpoint_rejects_bad_secret_without_insert():
    repository = FakeRepository()
    app = build_app(settings=make_settings(), repository=repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", json=body(secret="bad"))

    assert response.status_code == 401
    assert repository.events == []


def test_endpoint_rejects_malformed_body_without_insert():
    repository = FakeRepository()
    app = build_app(settings=make_settings(), repository=repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", content="{bad", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert repository.events == []
