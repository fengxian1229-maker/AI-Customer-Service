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


def make_message() -> dict:
    return {
        "id": 7,
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "payload_json": {"text": "hello"},
    }


def test_process_pending_message_marks_success_with_event_id():
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            return {"event_id": "event-1"}

    repository = FakeOutboundRepository()

    result = asyncio.run(process_pending_message(repository, SenderClient(), make_message()))

    assert result["status"] == "SENT"
    assert repository.sent == [7]
    assert repository.failures == []


def test_process_pending_message_marks_retryable_failure():
    class SenderClient:
        async def send_text(self, chat_id: str, thread_id: str | None, text: str) -> dict:
            raise TimeoutError("timed out")

    repository = FakeOutboundRepository()

    result = asyncio.run(process_pending_message(repository, SenderClient(), make_message()))

    assert result["status"] == "RETRYABLE"
    assert repository.sent == []
    assert repository.failures == [(7, "RETRYABLE", "timed out", True)]


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
