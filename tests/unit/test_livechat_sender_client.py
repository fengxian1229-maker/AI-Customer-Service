import asyncio

from app.channels.livechat.sender_client import LiveChatSenderClient


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
            "ignore_agents_availability": True,
        },
    )
    assert calls[1][0] == "/agent/action/send_event"
