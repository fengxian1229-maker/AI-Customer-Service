import asyncio

from app.channels.livechat.sender_client import LiveChatApiError
from app.services.livechat_preview import LiveChatPreviewPublisher


class FakeSenderClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    async def send_event_preview(self, chat_id: str, text: str, custom_id: str | None = None) -> dict:
        self.calls.append((chat_id, text, custom_id))
        if self.fail:
            raise LiveChatApiError(500, {"error": {"message": "preview failed"}})
        return {"event_id": f"preview-{len(self.calls)}"}


def test_preview_publisher_throttles_by_interval_delta_and_max_updates():
    sender = FakeSenderClient()
    now = 0.0

    publisher = LiveChatPreviewPublisher(
        sender,
        chat_id="chat-1",
        inbound_event_id=11,
        min_interval_ms=700,
        min_delta_chars=4,
        max_updates=2,
        clock=lambda: now,
    )

    asyncio.run(publisher.publish_if_needed(""))
    asyncio.run(publisher.publish_if_needed("abcd"))
    asyncio.run(publisher.publish_if_needed("abcd"))
    now = 0.2
    asyncio.run(publisher.publish_if_needed("abcdefgh"))
    now = 0.8
    asyncio.run(publisher.publish_if_needed("abcdef"))
    asyncio.run(publisher.publish_if_needed("abcdefgh"))
    now = 1.6
    asyncio.run(publisher.publish_if_needed("abcdefghijkl"))
    now = 2.4
    asyncio.run(publisher.publish_if_needed("abcdefghijklmnop"))
    asyncio.run(publisher.flush("abcdefghijklmnop-final"))

    assert sender.calls == [
        ("chat-1", "abcd", "preview:11"),
        ("chat-1", "abcdefgh", "preview:11"),
    ]


def test_preview_publisher_flush_bypasses_throttle_but_not_max_updates():
    sender = FakeSenderClient()
    now = 0.0
    publisher = LiveChatPreviewPublisher(
        sender,
        chat_id="chat-1",
        inbound_event_id=11,
        min_interval_ms=700,
        min_delta_chars=24,
        max_updates=2,
        clock=lambda: now,
    )

    asyncio.run(publisher.publish_if_needed("first preview text with enough chars"))
    asyncio.run(publisher.flush("final preview text"))
    asyncio.run(publisher.flush("ignored after max updates"))

    assert sender.calls == [
        ("chat-1", "first preview text with enough chars", "preview:11"),
        ("chat-1", "final preview text", "preview:11"),
    ]


def test_preview_publisher_swallows_preview_failures():
    sender = FakeSenderClient(fail=True)
    publisher = LiveChatPreviewPublisher(
        sender,
        chat_id="chat-1",
        inbound_event_id=11,
        min_interval_ms=0,
        min_delta_chars=1,
    )

    asyncio.run(publisher.publish_if_needed("partial"))
    asyncio.run(publisher.flush("final"))

    assert sender.calls == [
        ("chat-1", "partial", "preview:11"),
        ("chat-1", "final", "preview:11"),
    ]
