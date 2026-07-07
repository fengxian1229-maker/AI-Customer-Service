from fastapi.testclient import TestClient

from app.api.app import build_app
from app.core.settings import Settings


class FakeRepository:
    def __init__(self, duplicate: bool = False, recent_group_ids=None) -> None:
        self.duplicate = duplicate
        self.recent_group_ids = set(recent_group_ids or [])
        self.events = []

    async def insert(self, event):
        self.events.append(event)
        return {"inserted": not self.duplicate, "duplicate": self.duplicate}

    async def fetch_recent_livechat_group_ids(self, chat_id):
        assert chat_id == "chat-1"
        return self.recent_group_ids


class FakeAuditRepository:
    def __init__(self) -> None:
        self.received = []
        self.completed = []
        self.failed = []

    async def insert_received(self, body):
        self.received.append(body)
        return len(self.received)

    async def mark_completed(self, audit_id, **kwargs):
        self.completed.append((audit_id, kwargs))

    async def mark_failed(self, audit_id, **kwargs):
        self.failed.append((audit_id, kwargs))


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
    audit_repository = FakeAuditRepository()
    app = build_app(settings=make_settings(), repository=repository, audit_repository=audit_repository)

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
    assert len(audit_repository.received) == 1
    assert audit_repository.completed == [
        (
            1,
            {
                "http_status": 202,
                "normalized_count": 1,
                "inserted_count": 1,
                "duplicate_count": 0,
                "ignored_count": 0,
            },
        )
    ]


def test_endpoint_reports_duplicate_insert():
    repository = FakeRepository(duplicate=True)
    app = build_app(settings=make_settings(), repository=repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", json=body())

    assert response.status_code == 202
    assert response.json()["inserted"] == 0
    assert response.json()["duplicates"] == 1


def test_endpoint_uses_recent_chat_group_ids_for_incoming_event_without_access():
    class FailingLiveChatClient:
        async def get_chat(self, chat_id):
            raise AssertionError("get_chat should not be called when recent group data exists")

    repository = FakeRepository(recent_group_ids={23})
    app = build_app(
        settings=make_settings(livechat_allowed_group_ids="23"),
        repository=repository,
        livechat_client=FailingLiveChatClient(),
    )
    payload = body()
    payload["payload"].pop("access")

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", json=payload)

    assert response.status_code == 202
    assert response.json()["ignored"] == 0
    assert repository.events[0].ignored is False
    assert repository.events[0].payload_json["chat_lookup"]["access"]["group_ids"] == [23]


def test_endpoint_rejects_bad_secret_without_insert():
    repository = FakeRepository()
    audit_repository = FakeAuditRepository()
    app = build_app(settings=make_settings(), repository=repository, audit_repository=audit_repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", json=body(secret="bad"))

    assert response.status_code == 401
    assert repository.events == []
    assert len(audit_repository.received) == 1
    assert audit_repository.completed == []
    assert audit_repository.failed[0][0] == 1
    assert audit_repository.failed[0][1]["http_status"] == 401


def test_endpoint_rejects_malformed_body_without_insert():
    repository = FakeRepository()
    app = build_app(settings=make_settings(), repository=repository)

    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/livechat", content="{bad", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert repository.events == []
