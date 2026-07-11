from abc import ABC, abstractmethod
from typing import Any
from app.domain.messages import CanonicalInboundEvent, OutboundMessage


class ChannelAdapter(ABC):
    """All chat platforms must implement this contract.

    The Gateway / AI layer only consumes CanonicalInboundEvent and OutboundMessage;
    it should not depend on Text.com-specific payload fields.
    """

    @abstractmethod
    def parse_webhook(self, body: dict[str, Any]) -> list[CanonicalInboundEvent]:
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, message: OutboundMessage) -> str:
        raise NotImplementedError
