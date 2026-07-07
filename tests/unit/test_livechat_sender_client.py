import asyncio
import io
from urllib import error

import pytest

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient


def test_deactivate_chat_posts_livechat_deactivate_action(monkeypatch):
    client = LiveChatSenderClient("https://livechat.example/v3.6", "account-1", "token-1")
    calls = []

    async def fake_post_json(path: str, body: dict) -> dict:
        calls.append((path, body))
        return {"success": True}

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = asyncio.run(client.deactivate_chat("chat-1"))

    assert result == {"success": True}
    assert calls == [("/agent/action/deactivate_chat", {"id": "chat-1"})]


def test_send_text_adds_agent_to_chat_before_send(monkeypatch):
    client = LiveChatSenderClient("https://livechat.example/v3.6", "account-1", "token-1", agent_email="bot@example.com")
    calls = []

    async def fake_post_json(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == "/agent/action/send_event":
            return {"event_id": "event-1"}
        return {"success": True}

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = asyncio.run(client.send_text("chat-1", "thread-1", "hello"))

    assert result == {"event_id": "event-1"}
    assert calls[0] == (
        "/agent/action/add_user_to_chat",
        {
            "chat_id": "chat-1",
            "user_id": "bot@example.com",
            "user_type": "agent",
            "visibility": "all",
            "ignore_requester_presence": True,
        },
    )
    assert "ignore_agents_availability" not in calls[0][1]
    assert calls[1][0] == "/agent/action/send_event"


def test_send_text_continues_to_send_event_when_add_user_returns_500(monkeypatch):
    client = LiveChatSenderClient("https://livechat.example/v3.6", "account-1", "token-1", agent_email="bot@example.com")
    calls = []

    async def fake_post_json(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == "/agent/action/add_user_to_chat":
            raise LiveChatApiError(500, {"path": path, "raw": "<HTML>edge error</HTML>"})
        return {"event_id": "event-1"}

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = asyncio.run(client.send_text("chat-1", "thread-1", "hello"))

    assert result == {"event_id": "event-1"}
    assert [call[0] for call in calls] == ["/agent/action/add_user_to_chat", "/agent/action/send_event"]


def test_send_buttons_continues_to_send_event_when_add_user_returns_500(monkeypatch):
    client = LiveChatSenderClient("https://livechat.example/v3.6", "account-1", "token-1", agent_email="bot@example.com")
    calls = []
    menu = {"rich_message": {"type": "rich_message", "template_id": "cards", "elements": [], "visibility": "all"}}

    async def fake_post_json(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == "/agent/action/add_user_to_chat":
            raise LiveChatApiError(500, {"path": path, "raw": "<HTML>edge error</HTML>"})
        return {"event_id": "event-1"}

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = asyncio.run(client.send_buttons("chat-1", "thread-1", menu))

    assert result == {"event_id": "event-1"}
    assert calls == [
        (
            "/agent/action/add_user_to_chat",
            {
                "chat_id": "chat-1",
                "user_id": "bot@example.com",
                "user_type": "agent",
                "visibility": "all",
                "ignore_requester_presence": True,
            },
        ),
        (
            "/agent/action/send_event",
            {
                "chat_id": "chat-1",
                "event": {"type": "rich_message", "template_id": "cards", "elements": []},
            },
        ),
    ]


def test_transfer_chat_to_group_keeps_ignore_agents_availability(monkeypatch):
    client = LiveChatSenderClient("https://livechat.example/v3.6", "account-1", "token-1")
    calls = []

    async def fake_post_json(path: str, body: dict) -> dict:
        calls.append((path, body))
        return {"success": True}

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = asyncio.run(
        client.transfer_chat_to_group(
            "chat-1",
            7,
            ignore_agents_availability=True,
            ignore_requester_presence=False,
        )
    )

    assert result == {"success": True}
    assert calls == [
        (
            "/agent/action/transfer_chat",
            {
                "id": "chat-1",
                "target": {"type": "group", "ids": [7]},
                "ignore_agents_availability": True,
                "ignore_requester_presence": False,
            },
        )
    ]


def test_post_json_error_includes_livechat_path(monkeypatch):
    client = LiveChatSenderClient("https://livechat.example/v3.6", "account-1", "token-1")

    def fake_urlopen(req, timeout):
        del req, timeout
        raise error.HTTPError(
            url="https://livechat.example/v3.6/agent/action/add_user_to_chat",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b"<HTML>edge error</HTML>"),
        )

    monkeypatch.setattr("app.channels.livechat.sender_client.request.urlopen", fake_urlopen)

    with pytest.raises(LiveChatApiError) as exc_info:
        client._post_json_sync("/agent/action/add_user_to_chat", {"chat_id": "chat-1"})

    assert exc_info.value.status == 500
    assert exc_info.value.data["path"] == "/agent/action/add_user_to_chat"
    assert exc_info.value.data["raw"] == "<HTML>edge error</HTML>"
