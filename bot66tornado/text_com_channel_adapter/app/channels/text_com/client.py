import logging
import httpx
from app.core.config import Settings

logger = logging.getLogger(__name__)


class TextComAgentChatClient:
    def __init__(self, settings: Settings):
        self._base_url = settings.text_com_api_base_url.rstrip("/")
        self._token = settings.text_com_agent_auth_token

    async def send_event(self, chat_id: str, text: str, visibility: str = "all") -> str:
        if not self._token:
            raise RuntimeError("TEXT_COM_AGENT_AUTH_TOKEN is required to send messages")

        url = f"{self._base_url}/agent/action/send_event"
        payload = {
            "chat_id": chat_id,
            "event": {
                "type": "message",
                "text": text,
                "visibility": visibility,
            },
        }
        headers = {
            "Authorization": f"Basic {self._token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            event_id = data.get("event_id")
            if not event_id:
                logger.warning("Text.com send_event response missing event_id: %s", data)
                return ""
            return event_id
