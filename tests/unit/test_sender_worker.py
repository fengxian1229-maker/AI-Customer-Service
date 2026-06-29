import asyncio

from app.channels.livechat.sender_client import LiveChatApiError
from app.workers import sender_worker
from app.workers.sender_worker import classify_send_result, process_pending_message


def classify_send_error(exc: Exception) -> dict:
    return sender_worker.classify_send_error(exc)


def test_classify_send_result_marks_success():
    result = classify_send_result({"success": True})

    assert result["status"] == "SENT"
    assert result["last_error"] is None


def test_classify_send_result_marks_livechat_event_id_success():
    result = classify_send_result({"event_id": "event-1"})

    assert result["status"] == "SENT"
    assert result["last_error"] is None


def test_classify_send_error_marks_auth_as_config_failure():
    result = classify_send_error(LiveChatApiError(401, {"error": {"message": "Unauthorized"}}))

    assert result["status"] == "FAILED_CONFIG"
    assert result["retryable"] is False
    assert "HTTP 401" in result["last_error"]


def test_classify_send_error_marks_429_as_retryable():
    result = classify_send_error(LiveChatApiError(429, {"error": {"message": "Too many requests"}}))

    assert result["status"] == "RETRYABLE"
    assert result["retryable"] is True
    assert "HTTP 429" in result["last_error"]


def test_classify_send_error_marks_closed_chat_as_business_failure():
    result = classify_send_error(LiveChatApiError(400, {"error": {"message": "Chat is closed"}}))

    assert result["status"] == "FAILED_BUSINESS"
    assert result["retryable"] is False
    assert "Chat is closed" in result["last_error"]


def test_classify_send_error_marks_inactive_chat_as_business_failure():
    result = classify_send_error(LiveChatApiError(422, {"error": {"message": "Chat not active"}}))

    assert result["status"] == "FAILED_BUSINESS"
    assert result["retryable"] is False
    assert "Chat not active" in result["last_error"]


def test_classify_send_error_marks_unknown_failure():
    result = classify_send_error(RuntimeError("unexpected shape"))

    assert result["status"] == "FAILED_UNKNOWN"
    assert result["retryable"] is False
    assert result["last_error"] == "unexpected shape"


class FakeOutboundRepository:
    def __init__(self) -> None:
        self.sent = []
        self.failures = []

    async def mark_sent(self, outbound_message_id: int) -> None:
        self.sent.append(outbound_message_id)

    async def mark_failed(self, outbound_message_id: int, status: str, error: str, retryable: bool) -> None:
        self.failures.append((outbound_message_id, status, error, retryable))


class FakeConversationMessageRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert_idempotent(self, message: dict) -> dict:
        self.inserted.append(message)
        return {"inserted": True, "duplicate": False, "id": 1}


def make_message() -> dict:
    return {
        "id": 7,
        "tenant_id": "default",
        "channel_type": "livechat",
        "conversation_id": "livechat:chat-1",
        "inbound_event_id": 11,
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "action_type": "send_event",
        "message_type": "text",
        "status": "PENDING",
        "payload_json": {"text": "hello"},
    }


def make_message_of_type(message_type: str, payload: dict, message_id: int = 7) -> dict:
    message = make_message()
    message["id"] = message_id
    message["message_type"] = message_type
    message["message_kind"] = message_type
    message["command_type"] = {
        "text": "livechat.send_text",
        "image": "livechat.send_image",
        "buttons": "livechat.buttons_preview",
    }.get(message_type, f"livechat.{message_type}")
    message["action_type"] = message["command_type"]
    message["payload_json"] = payload
    return message


def test_process_pending_message_marks_success_with_event_id():
    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            self.calls.append((chat_id, thread_id, text))
            return {"event_id": "event-1"}

    repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository()
    client = SenderClient()
    message = {
        **make_message(),
        "conversation_status": "AI_ACTIVE",
        "conversation_active_workflow": None,
    }

    result = asyncio.run(process_pending_message(repository, client, message, message_repository=message_repository))

    assert result["status"] == "SENT"
    assert client.calls == [("chat-1", "thread-1", "hello")]
    assert repository.sent == [7]
    assert repository.failures == []
    assert message_repository.inserted[0]["outbound_message_id"] == 7
    assert message_repository.inserted[0]["sender_role"] == "assistant"
    assert message_repository.inserted[0]["text_content"] == "hello"


def test_process_pending_message_skips_when_conversation_human_active():
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise AssertionError("bot outbound must not send after human handoff")

    repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository()
    message = {
        **make_message(),
        "conversation_status": "HUMAN_ACTIVE",
        "conversation_active_workflow": "human_handoff",
    }
    expected_error = "conversation is HUMAN_ACTIVE or human_handoff; bot outbound skipped"

    result = asyncio.run(process_pending_message(repository, SenderClient(), message, message_repository=message_repository))

    assert result == {
        "status": "SKIPPED_HUMAN_ACTIVE",
        "last_error": expected_error,
        "retryable": False,
    }
    assert repository.failures == [(7, "SKIPPED_HUMAN_ACTIVE", expected_error, False)]
    assert repository.sent == []
    assert message_repository.inserted == []


def test_process_pending_for_inbound_event_only_fetches_target_inbound(monkeypatch):
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            return {"event_id": "event-1"}

    class FakeRepository:
        def __init__(self, pool) -> None:
            self.pool = pool
            self.sent = []
            self.failures = []

        async def fetch_pending_by_inbound_event(self, inbound_event_id: int, limit: int = 20) -> list[dict]:
            assert inbound_event_id == 55
            assert limit == 3
            return [make_message()]

        async def mark_sent(self, outbound_message_id: int) -> None:
            self.sent.append(outbound_message_id)

        async def mark_failed(self, outbound_message_id: int, status: str, error: str, retryable: bool) -> None:
            self.failures.append((outbound_message_id, status, error, retryable))

    class FakeMessageRepository:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def insert_idempotent(self, message: dict) -> dict:
            return {"inserted": True, "duplicate": False, "id": 1}

    class FakeTransactionRepository:
        def __init__(self, pool, outbound_repository=None, conversation_message_repository=None) -> None:
            self.pool = pool
            self.outbound_repository = outbound_repository

        async def mark_sent_with_message(self, outbound_message_id: int, message_record: dict) -> dict:
            await self.outbound_repository.mark_sent(outbound_message_id)
            return {"message_insert": {"inserted": True}}

    monkeypatch.setattr(sender_worker, "OutboundMessageRepository", FakeRepository)
    monkeypatch.setattr(sender_worker, "ConversationMessageRepository", FakeMessageRepository)
    monkeypatch.setattr(sender_worker, "SenderTransactionRepository", FakeTransactionRepository)

    result = asyncio.run(sender_worker.process_pending_for_inbound_event(object(), SenderClient(), inbound_event_id=55, limit=3))

    assert result[0]["status"] == "SENT"
    assert result[0]["outbound_message_id"] == 7
    assert result[0]["inbound_event_id"] == 11


def test_process_pending_message_image_uses_mvp_url_text_fallback():
    class SenderClient:
        def __init__(self) -> None:
            self.sent_texts = []

        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            self.sent_texts.append(text)
            return {"event_id": "event-image-fallback"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = make_message_of_type(
        "image",
        {
            "asset_key": "deposit_step_1",
            "asset_ref": "https://cdn.example/deposit_step_1.png",
            "caption": "第一步：进入充值页面",
            "position": "after",
        },
    )

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert result["delivery_mode"] == "mvp_text_fallback"
    assert client.sent_texts == ["图片：https://cdn.example/deposit_step_1.png\n第一步：进入充值页面"]
    assert repository.sent == [7]


def test_process_pending_message_buttons_preview_is_skipped_without_crashing():
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise AssertionError("buttons preview should not send")

    repository = FakeOutboundRepository()
    message = make_message_of_type("buttons", {"menu_key": "deposit_menu"})

    result = asyncio.run(process_pending_message(repository, SenderClient(), message))

    assert result["status"] == "SKIPPED_PREVIEW"
    assert repository.failures == [(7, "SKIPPED_PREVIEW", "buttons preview is not sent by sender_worker", False)]


def test_process_pending_message_unknown_type_is_skipped_unsupported():
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise AssertionError("unknown message should not send")

    repository = FakeOutboundRepository()
    message = make_message_of_type("video", {"url": "https://cdn.example/video.mp4"})

    result = asyncio.run(process_pending_message(repository, SenderClient(), message))

    assert result["status"] == "SKIPPED_UNSUPPORTED"
    assert repository.failures == [(7, "SKIPPED_UNSUPPORTED", "unsupported outbound message_type: video", False)]


def test_process_pending_message_marks_retryable_failure():
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise TimeoutError("timed out")

    repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository()

    result = asyncio.run(process_pending_message(repository, SenderClient(), make_message(), message_repository=message_repository))

    assert result["status"] == "RETRYABLE"
    assert repository.sent == []
    assert repository.failures == [(7, "RETRYABLE", "timed out", True)]
    assert message_repository.inserted == []


def test_livechat_auth_header_uses_account_and_token():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    assert client.auth_header() == "Basic YWNjb3VudC0xOnRva2VuLTE="


def test_livechat_auth_header_accepts_preencoded_basic_token():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="YWNjb3VudC0xOnRva2VuLTE=",
    )

    assert client.auth_header() == "Basic YWNjb3VudC0xOnRva2VuLTE="


def test_livechat_auth_header_accepts_preencoded_basic_token_with_different_env_account():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="different-account",
        access_token="YWNjb3VudC0xOnRva2VuLTE=",
    )

    assert client.auth_header() == "Basic YWNjb3VudC0xOnRva2VuLTE="


def test_livechat_transfer_chat_to_group_posts_expected_payload_and_accepts_empty_response():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    calls = []

    class Client(LiveChatSenderClient):
        async def _post_json(self, path: str, body: dict) -> dict:
            calls.append((path, body, self.auth_header()))
            return {}

    client = Client(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    result = asyncio.run(
        client.transfer_chat_to_group(
            chat_id="PJ0MRSHTDG",
            group_id=23,
            ignore_agents_availability=True,
            ignore_requester_presence=False,
        )
    )

    assert result == {}
    assert calls == [
        (
            "/agent/action/transfer_chat",
            {
                "id": "PJ0MRSHTDG",
                "target": {"type": "group", "ids": [23]},
                "ignore_agents_availability": True,
                "ignore_requester_presence": False,
            },
            "Basic YWNjb3VudC0xOnRva2VuLTE=",
        )
    ]
