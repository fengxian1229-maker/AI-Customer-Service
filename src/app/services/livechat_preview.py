from __future__ import annotations

import logging
import time
from collections.abc import Callable


logger = logging.getLogger(__name__)


class LiveChatPreviewPublisher:
    def __init__(
        self,
        sender_client,
        chat_id: str,
        inbound_event_id: int,
        min_interval_ms: int = 700,
        min_delta_chars: int = 24,
        max_updates: int = 12,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.sender_client = sender_client
        self.chat_id = chat_id
        self.inbound_event_id = inbound_event_id
        self.min_interval_seconds = max(0, int(min_interval_ms)) / 1000
        self.min_delta_chars = max(0, int(min_delta_chars))
        self.max_updates = max(0, int(max_updates))
        self.clock = clock or time.monotonic
        self.custom_id = f"preview:{inbound_event_id}"
        self._last_text = ""
        self._last_sent_at: float | None = None
        self._updates_sent = 0

    async def publish_if_needed(self, text: str) -> None:
        text = str(text or "").strip()
        if not text or not self._can_publish(text):
            return
        if self._last_sent_at is not None and self.clock() - self._last_sent_at < self.min_interval_seconds:
            return
        if len(text) - len(self._last_text) < self.min_delta_chars:
            return
        await self._publish(text)

    async def flush(self, text: str) -> None:
        text = str(text or "").strip()
        if not text or not self._can_publish(text):
            return
        await self._publish(text)

    def _can_publish(self, text: str) -> bool:
        if self.max_updates <= 0:
            return False
        if self._updates_sent >= self.max_updates:
            return False
        return text != self._last_text

    async def _publish(self, text: str) -> None:
        try:
            await self.sender_client.send_event_preview(self.chat_id, text, custom_id=self.custom_id)
        except Exception:
            logger.exception(
                "livechat preview publish failed",
                extra={"chat_id": self.chat_id, "inbound_event_id": self.inbound_event_id},
            )
        finally:
            self._last_text = text
            self._last_sent_at = self.clock()
            self._updates_sent += 1
