import asyncio
import base64
import json
import mimetypes
import uuid
from pathlib import Path
from urllib import error, request
from urllib.parse import unquote, urlparse


class LiveChatSenderClient:
    def __init__(self, base_url: str, account_id: str, access_token: str, agent_email: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.access_token = access_token
        self.agent_email = str(agent_email or "").strip() or None

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

    async def send_text(self, chat_id: str, thread_id: str | None, text: str, custom_id: str | None = None) -> dict:
        del thread_id
        await self._ensure_agent_added_before_send(chat_id)
        event = {
            "type": "message",
            "text": text,
        }
        if custom_id:
            event["custom_id"] = custom_id
        body = {
            "chat_id": chat_id,
            "event": event,
        }
        return await self._post_json("/agent/action/send_event", body)

    async def send_event_preview(self, chat_id: str, text: str, custom_id: str | None = None) -> dict:
        event = {
            "type": "message",
            "text": text,
            "visibility": "all",
        }
        if custom_id:
            event["custom_id"] = custom_id
        body = {
            "chat_id": chat_id,
            "event": event,
        }
        return await self._post_json("/agent/action/send_event_preview", body)

    async def send_typing_indicator(self, chat_id: str, is_typing: bool = True) -> dict:
        body = {
            "chat_id": chat_id,
            "is_typing": is_typing,
            "visibility": "all",
        }
        return await self._post_json("/agent/action/send_typing_indicator", body)

    async def send_thinking_indicator(
        self,
        chat_id: str,
        title: str = "正在处理...",
        description: str = "我正在生成回复，请稍等。",
        custom_id: str | None = None,
    ) -> dict:
        body = {
            "chat_id": chat_id,
            "title": title,
            "description": description,
            "visibility": "all",
        }
        if custom_id:
            body["custom_id"] = custom_id
        return await self._post_json("/agent/action/send_thinking_indicator", body)

    async def send_buttons(self, chat_id: str, thread_id: str | None, menu: dict) -> dict:
        del thread_id
        await self._ensure_agent_added_before_send(chat_id)
        event = dict(menu["rich_message"])
        event.pop("visibility", None)
        body = {
            "chat_id": chat_id,
            "event": event,
        }
        return await self._post_json("/agent/action/send_event", body)

    async def send_image(
        self,
        chat_id: str,
        thread_id: str | None,
        asset_ref: str,
        filename: str | None = None,
    ) -> dict:
        del thread_id
        await self._ensure_agent_added_before_send(chat_id)
        file_data = await self._load_file(asset_ref, filename=filename)
        uploaded = await self.upload_file(
            file_data["content"],
            content_type=file_data["content_type"],
            filename=file_data["filename"],
        )
        url = str(uploaded.get("url") or "").strip()
        if not url:
            raise LiveChatApiError(
                502,
                {
                    "path": "/agent/action/upload_file",
                    "error": {"message": f"upload_file returned no url: {uploaded}"},
                },
            )
        body = {
            "chat_id": chat_id,
            "event": {
                "type": "file",
                "url": url,
                "name": file_data["filename"],
                "content_type": file_data["content_type"],
            },
        }
        return await self._post_json("/agent/action/send_event", body)

    async def upload_file(self, content: bytes, content_type: str, filename: str) -> dict:
        return await asyncio.to_thread(self._upload_file_sync, content, content_type, filename)

    async def list_chats(self, limit: int = 20) -> list[dict]:
        data = await self._post_json("/agent/action/list_chats", {
            "sort_order": "desc",
            "limit": limit,
        })
        return data.get("chats_summary") or []

    async def get_chat(self, chat_id: str) -> dict:
        data = await self._post_json("/agent/action/get_chat", {"chat_id": chat_id})
        return data.get("chat") or data

    async def list_archives(self, filters: dict | None = None, limit: int = 20, page_id: str | None = None) -> dict:
        body = {
            "filters": filters or {},
            "limit": limit,
        }
        if page_id:
            body["page_id"] = page_id
        return await self._post_json("/agent/action/list_archives", body)

    async def add_user_to_chat(self, chat_id: str) -> dict:
        if not self.agent_email:
            return {"skipped": True, "reason": "livechat_agent_email_not_configured"}
        body = {
            "chat_id": chat_id,
            "user_id": self.agent_email,
            "user_type": "agent",
            "visibility": "all",
            "ignore_requester_presence": True,
        }
        try:
            return await self._post_json("/agent/action/add_user_to_chat", body)
        except LiveChatApiError as exc:
            if exc.status in {400, 409, 422} and _looks_like_already_joined_error(exc.data):
                return {"skipped": True, "reason": "already_joined"}
            raise

    async def _ensure_agent_added_before_send(self, chat_id: str) -> dict:
        try:
            return await self.add_user_to_chat(chat_id)
        except LiveChatApiError as exc:
            if exc.status == 500 and exc.data.get("path") == "/agent/action/add_user_to_chat":
                return {"skipped": True, "reason": "add_user_to_chat_server_error", "error": str(exc)}
            raise

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

    async def deactivate_chat(self, chat_id: str) -> dict:
        return await self._post_json("/agent/action/deactivate_chat", {"id": chat_id})

    async def _post_json(self, path: str, body: dict) -> dict:
        return await asyncio.to_thread(self._post_json_sync, path, body)

    async def _load_file(self, source: str, filename: str | None = None) -> dict:
        return await asyncio.to_thread(self._load_file_sync, source, filename)

    def _load_file_sync(self, source: str, filename: str | None = None) -> dict:
        source = str(source or "").strip()
        if not source:
            raise ValueError("image asset_ref is required")
        if source.startswith("http://") or source.startswith("https://"):
            return self._load_remote_file_sync(source, filename=filename)
        return self._load_local_file_sync(source, filename=filename)

    def _load_remote_file_sync(self, url: str, filename: str | None = None) -> dict:
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=30) as response:
            content = response.read()
            content_type = response.headers.get_content_type() or _content_type_for_name(url)
        return {
            "content": content,
            "content_type": content_type,
            "filename": filename or _filename_for_source(url, content_type),
        }

    def _load_local_file_sync(self, source: str, filename: str | None = None) -> dict:
        parsed = urlparse(source)
        raw_path = unquote(parsed.path) if parsed.scheme == "file" else source
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        content = path.read_bytes()
        content_type = _content_type_for_name(path.name)
        return {
            "content": content,
            "content_type": content_type,
            "filename": filename or path.name,
        }

    def _upload_file_sync(self, content: bytes, content_type: str, filename: str) -> dict:
        boundary = f"LiveChatBoundary{uuid.uuid4().hex}"
        body = b"".join(
            [
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8"),
                content,
                f"\r\n--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        req = request.Request(
            f"{self.base_url}/agent/action/upload_file",
            data=body,
            method="POST",
            headers={
                "Authorization": self.auth_header(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
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
            raise LiveChatApiError(exc.code, _with_livechat_path(data, "/agent/action/upload_file")) from exc

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
            raise LiveChatApiError(exc.code, _with_livechat_path(data, path)) from exc


class LiveChatApiError(RuntimeError):
    def __init__(self, status: int, data: dict) -> None:
        self.status = status
        self.data = data
        super().__init__(f"LiveChat API returned HTTP {status}: {data}")


def _with_livechat_path(data: dict, path: str) -> dict:
    return {**data, "path": path}


def _content_type_for_name(name: str) -> str:
    content_type, _ = mimetypes.guess_type(str(name))
    if content_type:
        return content_type
    return "application/octet-stream"


def _looks_like_already_joined_error(data: dict) -> bool:
    raw = json.dumps(data, ensure_ascii=False).lower()
    markers = (
        "already",
        "already exists",
        "already in chat",
        "already a member",
        "is already",
    )
    return any(marker in raw for marker in markers)


def _filename_for_source(source: str, content_type: str) -> str:
    parsed = urlparse(source)
    name = Path(unquote(parsed.path)).name
    if name:
        return name
    extension = mimetypes.guess_extension(content_type) or ".bin"
    return f"image{extension}"
