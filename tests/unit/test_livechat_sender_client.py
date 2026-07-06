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
