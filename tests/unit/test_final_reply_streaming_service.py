import asyncio
import json

from app.core.settings import Settings
from app.services.final_reply_streaming_service import FinalReplyStreamingService
from app.workflows.final_reply_policy import build_reply_plan


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeModel:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        for chunk in self.chunks:
            yield FakeChunk(chunk)


class FakePreviewPublisher:
    def __init__(self) -> None:
        self.published = []
        self.flushed = []

    async def publish_if_needed(self, text: str) -> None:
        self.published.append(text)

    async def flush(self, text: str) -> None:
        self.flushed.append(text)


def make_state() -> dict:
    fallback = "请提供用户名或注册手机号。"
    return {
        "tenant_id": "default",
        "channel_type": "livechat",
        "conversation_id": "livechat:chat-1",
        "raw_user_input": "存款没到",
        "rewritten_question": "存款没到",
        "route": "final_reply",
        "intent_result": {"intent": "deposit_missing", "route": "final_reply"},
        "reply_language": "zh-Hans",
        "response_text": fallback,
        "response_text_fallback": fallback,
        "reply_plan": build_reply_plan(
            kind="ask_missing_slots",
            fallback_text=fallback,
            must_say=["用户名", "注册手机号"],
            must_not_say=["已到账"],
            allowed_facts=[fallback],
        ),
        "commands": [{"type": "livechat.send_text", "payload": {"text": fallback}}],
    }


def test_final_reply_streaming_service_publishes_chunks_and_returns_final_text(monkeypatch):
    model = FakeModel(["您好，", "请提供用户名", "或注册手机号。"])
    monkeypatch.setattr("app.services.final_reply_streaming_service.build_gemini_chat_model", lambda settings: model)
    publisher = FakePreviewPublisher()
    service = FinalReplyStreamingService(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(service.stream_final_reply(make_state(), publisher))

    assert publisher.published == ["您好，", "您好，请提供用户名", "您好，请提供用户名或注册手机号。"]
    assert publisher.flushed == ["您好，请提供用户名或注册手机号。"]
    assert result["final_response_text"] == "您好，请提供用户名或注册手机号。"
    assert result["response_text_fallback"] == "请提供用户名或注册手机号。"
    assert result["final_reply_result"]["status"] == "accepted"
    assert model.messages[0][0] == "system"
    assert "Final Reply Composer" in model.messages[0][1]
    assert "Return only structured JSON" not in model.messages[0][1]
    assert "Return only the final customer-visible reply text" in model.messages[0][1]


def test_final_reply_streaming_service_extracts_json_text_without_previewing_json(monkeypatch):
    streamed_json = json.dumps(
        {
            "text": "您好，请提供用户名或注册手机号。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.92,
            "safety_flags": [],
            "used_facts": [],
            "reason": "test",
        },
        ensure_ascii=False,
    )
    model = FakeModel([streamed_json[:20], streamed_json[20:]])
    monkeypatch.setattr("app.services.final_reply_streaming_service.build_gemini_chat_model", lambda settings: model)
    publisher = FakePreviewPublisher()
    service = FinalReplyStreamingService(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(service.stream_final_reply(make_state(), publisher))

    assert publisher.published == []
    assert publisher.flushed == ["您好，请提供用户名或注册手机号。"]
    assert result["final_response_text"] == "您好，请提供用户名或注册手机号。"
    assert result["final_reply_result"]["status"] == "accepted"


def test_final_reply_streaming_service_extracts_fenced_json_text(monkeypatch):
    model = FakeModel(
        [
            '```json\n{"text": "您好，请提供用户名或注册手机号。", '
            '"language": "zh-Hans", "tone": "polite", "confidence": 0.9, '
            '"safety_flags": [], "used_facts": [], "reason": "test"}\n```'
        ]
    )
    monkeypatch.setattr("app.services.final_reply_streaming_service.build_gemini_chat_model", lambda settings: model)
    publisher = FakePreviewPublisher()
    service = FinalReplyStreamingService(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(service.stream_final_reply(make_state(), publisher))

    assert publisher.published == []
    assert publisher.flushed == ["您好，请提供用户名或注册手机号。"]
    assert result["final_response_text"] == "您好，请提供用户名或注册手机号。"


def test_final_reply_streaming_service_guardrail_failure_is_audited(monkeypatch):
    model = FakeModel(["您的存款已到账。"])
    monkeypatch.setattr("app.services.final_reply_streaming_service.build_gemini_chat_model", lambda settings: model)
    publisher = FakePreviewPublisher()
    service = FinalReplyStreamingService(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(service.stream_final_reply(make_state(), publisher))

    assert publisher.flushed == ["您的存款已到账。"]
    assert result["final_response_text"] == "您的存款已到账。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert result["final_reply_result"]["warning_reason"] == "guardrail_audit"
    assert "forbidden_backend_fact" in result["final_reply_result"]["violations"]


def test_final_reply_streaming_service_empty_model_text_returns_fallback(monkeypatch):
    model = FakeModel([""])
    monkeypatch.setattr("app.services.final_reply_streaming_service.build_gemini_chat_model", lambda settings: model)
    publisher = FakePreviewPublisher()
    service = FinalReplyStreamingService(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(service.stream_final_reply(make_state(), publisher))

    assert publisher.flushed == ["请提供用户名或注册手机号。"]
    assert result["final_response_text"] == "请提供用户名或注册手机号。"
    assert result["final_reply_result"]["status"] == "fallback"
    assert result["final_reply_result"]["fallback_reason"] == "empty_model_text"
