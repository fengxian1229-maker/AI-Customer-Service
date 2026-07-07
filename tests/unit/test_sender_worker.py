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


def test_classify_send_error_stops_retrying_after_retry_limit():
    result = sender_worker.classify_send_error(
        LiveChatApiError(500, {"raw": "<HTML>edge error</HTML>", "path": "/agent/action/send_event"}),
        retry_count=11,
    )

    assert result["status"] == "FAILED_BUSINESS"
    assert result["retryable"] is False
    assert "retry limit reached" in result["last_error"]


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
        "buttons": "livechat.send_buttons",
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


def test_process_pending_message_passes_text_custom_id_to_livechat_send_event():
    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(
            self,
            chat_id: str,
            thread_id: str | None,
            text: str,
            custom_id: str | None = None,
        ) -> dict:
            self.calls.append((chat_id, thread_id, text, custom_id))
            return {"event_id": "event-1"}

    repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository()
    client = SenderClient()
    message = {
        **make_message(),
        "payload_json": {"text": "streamed final", "custom_id": "preview:123"},
        "inbound_event_id": 123,
        "conversation_status": "AI_ACTIVE",
        "conversation_active_workflow": None,
    }

    result = asyncio.run(process_pending_message(repository, client, message, message_repository=message_repository))

    assert result["status"] == "SENT"
    assert client.calls == [("chat-1", "thread-1", "streamed final", None)]
    assert repository.sent == [7]
    assert message_repository.inserted[0]["text_content"] == "streamed final"


def test_process_pending_message_passes_non_preview_custom_id_unchanged():
    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(
            self,
            chat_id: str,
            thread_id: str | None,
            text: str,
            custom_id: str | None = None,
        ) -> dict:
            self.calls.append((chat_id, thread_id, text, custom_id))
            return {"event_id": "event-1"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = {
        **make_message(),
        "payload_json": {"text": "plain final", "custom_id": "manual:123"},
        "conversation_status": "AI_ACTIVE",
        "conversation_active_workflow": None,
    }

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert client.calls == [("chat-1", "thread-1", "plain final", "manual:123")]


def test_process_pending_message_rewrites_legacy_final_custom_id_colon():
    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(
            self,
            chat_id: str,
            thread_id: str | None,
            text: str,
            custom_id: str | None = None,
        ) -> dict:
            self.calls.append((chat_id, thread_id, text, custom_id))
            return {"event_id": "event-1"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = {
        **make_message(),
        "payload_json": {"text": "legacy final", "custom_id": "final:123"},
        "inbound_event_id": 123,
        "conversation_status": "AI_ACTIVE",
        "conversation_active_workflow": None,
    }

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert client.calls == [("chat-1", "thread-1", "legacy final", None)]


def test_process_pending_message_omits_generated_final_custom_id_dash():
    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_text(
            self,
            chat_id: str,
            thread_id: str | None,
            text: str,
            custom_id: str | None = None,
        ) -> dict:
            self.calls.append((chat_id, thread_id, text, custom_id))
            return {"event_id": "event-1"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = {
        **make_message(),
        "payload_json": {"text": "generated final", "custom_id": "final-123"},
        "inbound_event_id": 123,
        "conversation_status": "AI_ACTIVE",
        "conversation_active_workflow": None,
    }

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert client.calls == [("chat-1", "thread-1", "generated final", None)]


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


def test_process_pending_message_allows_handoff_ack_when_conversation_human_active():
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
        "conversation_status": "HUMAN_ACTIVE",
        "conversation_active_workflow": "human_handoff",
        "payload_json": {"type": "message", "text": "我会为你转接真人客服继续协助。", "handoff_ack": True},
    }

    result = asyncio.run(process_pending_message(repository, client, message, message_repository=message_repository))

    assert result["status"] == "SENT"
    assert client.calls == [("chat-1", "thread-1", "我会为你转接真人客服继续协助。")]
    assert repository.sent == [7]
    assert repository.failures == []
    assert message_repository.inserted[0]["text_content"] == "我会为你转接真人客服继续协助。"


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


def test_process_pending_message_image_sends_livechat_file():
    class SenderClient:
        def __init__(self) -> None:
            self.sent_images = []
            self.sent_texts = []

        async def send_image(self, chat_id: str, thread_id: str | None, asset_ref: str) -> dict:
            self.sent_images.append((chat_id, thread_id, asset_ref))
            return {"event_id": "event-image"}

    repository = FakeOutboundRepository()
    message_repository = FakeConversationMessageRepository()
    client = SenderClient()
    message = make_message_of_type(
        "image",
        {
            "asset_key": "deposit_step_1",
            "asset_ref": "https://cdn.example/deposit_step_1.png",
            "caption": "",
            "position": "after",
        },
    )

    result = asyncio.run(process_pending_message(repository, client, message, message_repository=message_repository))

    assert result["status"] == "SENT"
    assert result["delivery_mode"] == "livechat_file"
    assert client.sent_images == [("chat-1", "thread-1", "https://cdn.example/deposit_step_1.png")]
    assert client.sent_texts == []
    assert repository.sent == [7]
    assert message_repository.inserted[0]["message_type"] == "image"
    assert message_repository.inserted[0]["text_content"] == ""


def test_process_pending_message_image_sends_caption_after_file():
    class SenderClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_image(self, chat_id: str, thread_id: str | None, asset_ref: str) -> dict:
            self.calls.append(("image", chat_id, thread_id, asset_ref))
            return {"event_id": "event-image"}

        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            self.calls.append(("text", chat_id, thread_id, text))
            return {"event_id": "event-caption"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = make_message_of_type(
        "image",
        {
            "asset_key": "deposit_step_1",
            "asset_ref": "bot66tornado/assets/tutorials/JUE999/deposit.jpg",
            "caption": "第一步：进入充值页面",
        },
    )

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert result["delivery_mode"] == "livechat_file"
    assert result["caption_result"] == {"status": "SENT", "last_error": None}
    assert client.calls == [
        ("image", "chat-1", "thread-1", "bot66tornado/assets/tutorials/JUE999/deposit.jpg"),
        ("text", "chat-1", "thread-1", "第一步：进入充值页面"),
    ]
    assert repository.sent == [7]


def test_process_pending_message_image_upload_failure_marks_failed_without_fallback(monkeypatch):
    monkeypatch.delenv("LIVECHAT_IMAGE_TEXT_FALLBACK", raising=False)

    class SenderClient:
        def __init__(self) -> None:
            self.sent_texts = []

        async def send_image(self, chat_id: str, thread_id: str | None, asset_ref: str) -> dict:
            raise TimeoutError("upload timed out")

        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            self.sent_texts.append(text)
            return {"event_id": "must-not-send"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = make_message_of_type("image", {"asset_key": "deposit_step_1", "asset_ref": "missing.jpg"})

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "RETRYABLE"
    assert repository.sent == []
    assert repository.failures == [(7, "RETRYABLE", "upload timed out", True)]
    assert client.sent_texts == []


def test_process_pending_message_image_can_use_text_fallback_when_enabled(monkeypatch):
    monkeypatch.setenv("LIVECHAT_IMAGE_TEXT_FALLBACK", "true")

    class SenderClient:
        def __init__(self) -> None:
            self.sent_texts = []

        async def send_image(self, chat_id: str, thread_id: str | None, asset_ref: str) -> dict:
            raise RuntimeError("upload failed")

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
        },
    )

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert result["delivery_mode"] == "mvp_text_fallback"
    assert client.sent_texts == ["图片：https://cdn.example/deposit_step_1.png\n第一步：进入充值页面"]
    assert repository.sent == [7]


def test_process_pending_message_buttons_sends_quick_replies():
    class SenderClient:
        def __init__(self) -> None:
            self.sent_buttons = []

        async def send_buttons(self, chat_id: str, thread_id: str | None, menu: dict) -> dict:
            self.sent_buttons.append((chat_id, thread_id, menu))
            return {"event_id": "event-buttons"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = make_message_of_type("buttons", {"menu_key": "deposit_menu"})

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result["status"] == "SENT"
    assert repository.sent == [7]
    rich = client.sent_buttons[0][2]["rich_message"]
    assert rich["template_id"] == "quick_replies"
    assert rich["elements"][0]["buttons"][0]["postback_id"] == "main_deposito"
    assert "value" not in rich["elements"][0]["buttons"][0]


def test_process_pending_message_buttons_falls_back_to_text_when_rich_send_fails():
    class SenderClient:
        def __init__(self) -> None:
            self.sent_texts = []

        async def send_buttons(self, chat_id: str, thread_id: str | None, menu: dict) -> dict:
            raise RuntimeError("rich failed")

        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            self.sent_texts.append(text)
            return {"event_id": "event-fallback"}

    repository = FakeOutboundRepository()
    client = SenderClient()
    message = make_message_of_type("buttons", {"menu_key": "deposit", "language": "en"})

    result = asyncio.run(process_pending_message(repository, client, message))

    assert result == {"status": "SENT", "last_error": None, "retryable": False, "delivery_mode": "buttons_text_fallback"}
    assert repository.sent == [7]
    assert client.sent_texts == ["Choose the deposit issue:\n\n1. 🧾 Deposit not credited\n2. 📘 How to deposit"]


def test_process_pending_message_buttons_unknown_menu_fails_without_retry():
    class SenderClient:
        async def send_buttons(self, chat_id: str, thread_id: str | None, menu: dict) -> dict:
            raise AssertionError("unknown menu should not send")

    repository = FakeOutboundRepository()
    message = make_message_of_type("buttons", {"menu_key": "missing"})

    result = asyncio.run(process_pending_message(repository, SenderClient(), message))

    assert result["status"] == "FAILED_UNKNOWN"
    assert repository.failures == [(7, "FAILED_UNKNOWN", "'unknown livechat menu_key: missing'", False)]


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


def test_livechat_send_event_preview_posts_expected_payload():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    calls = []

    class Client(LiveChatSenderClient):
        async def _post_json(self, path: str, body: dict) -> dict:
            calls.append((path, body))
            return {"event_id": "preview-1"}

    client = Client(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    result = asyncio.run(client.send_event_preview("chat-1", "partial reply", custom_id="preview:event-1"))

    assert result == {"event_id": "preview-1"}
    assert calls == [
        (
            "/agent/action/send_event_preview",
            {
                "chat_id": "chat-1",
                "event": {
                    "type": "message",
                    "text": "partial reply",
                    "visibility": "all",
                    "custom_id": "preview:event-1",
                },
            },
        )
    ]


def test_livechat_send_text_posts_custom_id_when_present():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    calls = []

    class Client(LiveChatSenderClient):
        async def _post_json(self, path: str, body: dict) -> dict:
            calls.append((path, body))
            return {"event_id": "event-1"}

    client = Client(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    result = asyncio.run(client.send_text("chat-1", "thread-1", "final reply", custom_id="preview:event-1"))

    assert result == {"event_id": "event-1"}
    assert calls == [
        (
            "/agent/action/send_event",
            {
                "chat_id": "chat-1",
                "event": {
                    "type": "message",
                    "text": "final reply",
                    "custom_id": "preview:event-1",
                },
            },
        )
    ]


def test_livechat_send_event_preview_omits_empty_custom_id():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    calls = []

    class Client(LiveChatSenderClient):
        async def _post_json(self, path: str, body: dict) -> dict:
            calls.append((path, body))
            return {}

    client = Client(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    asyncio.run(client.send_event_preview("chat-1", "partial reply"))

    assert "custom_id" not in calls[0][1]["event"]


def test_livechat_typing_and_thinking_indicators_post_expected_payloads():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    calls = []

    class Client(LiveChatSenderClient):
        async def _post_json(self, path: str, body: dict) -> dict:
            calls.append((path, body))
            return {}

    client = Client(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    asyncio.run(client.send_typing_indicator("chat-1", is_typing=True))
    asyncio.run(client.send_thinking_indicator("chat-1", title="处理中", description="请稍等", custom_id="think-1"))

    assert calls == [
        (
            "/agent/action/send_typing_indicator",
            {"chat_id": "chat-1", "is_typing": True, "visibility": "all"},
        ),
        (
            "/agent/action/send_thinking_indicator",
            {
                "chat_id": "chat-1",
                "title": "处理中",
                "description": "请稍等",
                "visibility": "all",
                "custom_id": "think-1",
            },
        ),
    ]


def test_livechat_send_image_uploads_local_file_and_sends_file_event(tmp_path):
    from app.channels.livechat.sender_client import LiveChatSenderClient

    image_path = tmp_path / "deposit.jpg"
    image_path.write_bytes(b"fake-jpeg")
    calls = []

    class Client(LiveChatSenderClient):
        async def upload_file(self, content: bytes, content_type: str, filename: str) -> dict:
            calls.append(("upload", content, content_type, filename))
            return {"url": "https://files.example/deposit.jpg"}

        async def _post_json(self, path: str, body: dict) -> dict:
            calls.append(("post", path, body))
            return {"event_id": "event-image"}

    client = Client(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    result = asyncio.run(client.send_image("chat-1", "thread-1", str(image_path)))

    assert result == {"event_id": "event-image"}
    assert calls == [
        ("upload", b"fake-jpeg", "image/jpeg", "deposit.jpg"),
        (
            "post",
            "/agent/action/send_event",
            {
                "chat_id": "chat-1",
                "event": {
                    "type": "file",
                    "url": "https://files.example/deposit.jpg",
                    "name": "deposit.jpg",
                    "content_type": "image/jpeg",
                },
            },
        ),
    ]


def test_livechat_upload_file_posts_multipart(monkeypatch):
    from app.channels.livechat import sender_client as sender_client_module
    from app.channels.livechat.sender_client import LiveChatSenderClient

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return b'{"url":"https://files.example/reply.png"}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return Response()

    monkeypatch.setattr(sender_client_module.request, "urlopen", fake_urlopen)
    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    result = client._upload_file_sync(b"png-bytes", "image/png", "reply.png")

    assert result == {"url": "https://files.example/reply.png"}
    assert captured["url"] == "https://api.livechatinc.com/v3.6/agent/action/upload_file"
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Basic YWNjb3VudC0xOnRva2VuLTE="
    assert captured["headers"]["Content-type"].startswith("multipart/form-data; boundary=LiveChatBoundary")
    assert b'Content-Disposition: form-data; name="file"; filename="reply.png"' in captured["body"]
    assert b"Content-Type: image/png" in captured["body"]
    assert b"png-bytes" in captured["body"]
