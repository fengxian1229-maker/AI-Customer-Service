import asyncio
import base64
import json
from urllib import error, request


class LiveChatSenderClient:
    def __init__(self, base_url: str, account_id: str, access_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.access_token = access_token

    def auth_header(self) -> str:
        if self._looks_like_basic_token(self.access_token):
            return f"Basic {self.access_token}"
        raw = f"{self.account_id}:{self.access_token}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    def _looks_like_basic_token(self, token: str) -> bool:
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except Exception:
            return False
        return ":" in decoded

    async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
        body = {
            "chat_id": chat_id,
            "event": {
                "type": "message",
                "text": text,
                "visibility": "all",
            },
        }
        return await self._post_json("/agent/action/send_event", body)

    async def list_chats(self, limit: int = 20) -> list[dict]:
        data = await self._post_json("/agent/action/list_chats", {
            "sort_order": "desc",
            "limit": limit,
        })
        return data.get("chats_summary") or []

    async def get_chat(self, chat_id: str) -> dict:
        data = await self._post_json("/agent/action/get_chat", {"chat_id": chat_id})
        return data.get("chat") or data

    async def transfer_chat_to_group(
        self,
        chat_id: str,
        group_id: int,
        ignore_agents_availability: bool = True,
        ignore_requester_presence: bool = True,
    ) -> dict:
        body = {
            "id": chat_id,
            "target": {"type": "group", "ids": [group_id]},
            "ignore_agents_availability": ignore_agents_availability,
            "ignore_requester_presence": ignore_requester_presence,
        }
        return await self._post_json("/agent/action/transfer_chat", body)

    async def _post_json(self, path: str, body: dict) -> dict:
        return await asyncio.to_thread(self._post_json_sync, path, body)

    def _post_json_sync(self, path: str, body: dict) -> dict:
        raw_body = json.dumps(body).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=raw_body,
            method="POST",
            headers={
                "Authorization": self.auth_header(),
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except error.HTTPError as exc:
            payload = exc.read().decode("utf-8")
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {"raw": payload}
            raise LiveChatApiError(exc.code, data) from exc


class LiveChatApiError(RuntimeError):
    def __init__(self, status: int, data: dict) -> None:
        self.status = status
        self.data = data
        super().__init__(f"LiveChat API returned HTTP {status}: {data}")
