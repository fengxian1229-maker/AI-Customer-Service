from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.schemas.events import InboundEvent


@dataclass(frozen=True)
class IngressEvent:
    source: str
    raw_action: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class IngressNormalizeResult:
    event: InboundEvent | None
    ignored: bool = False
    ignore_reason: str | None = None


class BaseIngressReceiver(ABC):
    @abstractmethod
    async def receive_once(self, limit: int = 20) -> dict[str, Any]:
        """Normalize one ingress batch into inbound_events."""
