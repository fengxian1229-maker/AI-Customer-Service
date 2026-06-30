import asyncio

from app.services.final_reply_service import FinalReplyService
from app.workflows.final_reply_policy import build_reply_plan


class FakeFinalReplyProvider:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = []

    async def compose_final_reply(self, payload: dict) -> dict:
        self.calls.append(payload)
        return self.result


def base_state(**overrides):
    state = {
        "tenant_id": "default",
        "channel_type": "livechat",
        "conversation_id": "livechat:chat-1",
        "raw_user_input": "Please help with my deposit",
        "rewritten_question": "Please help with my deposit",
        "recent_messages": [],
        "route": "sop",
        "intent_result": {"intent": "deposit_missing", "route": "sop"},
        "active_workflow": "deposit_missing",
        "workflow_stage": "collecting_slots",
        "status": "AI_ACTIVE",
        "slot_memory": {},
        "missing_slots": ["account_or_phone", "deposit_screenshot"],
        "sop_action": "ask_missing_slots",
        "rag_result": None,
        "response_text": "请提供用户名或注册手机号，并上传存款付款截图。",
        "response_text_fallback": "请提供用户名或注册手机号，并上传存款付款截图。",
        "detected_language": "en",
        "language_confidence": 0.92,
        "language_source": "deterministic",
        "conversation_language": "en",
        "reply_language": "en",
        "language_result": {"reply_language": "en", "reason": "current message"},
        "supported_languages": ["zh-Hans", "en", "tl", "th"],
        "reply_plan": build_reply_plan(
            kind="ask_missing_slots",
            fallback_text="请提供用户名或注册手机号，并上传存款付款截图。",
            must_say=["用户名或注册手机号", "存款付款截图"],
            semantic_required_items=["account_or_phone", "deposit_screenshot"],
            missing_slots=["account_or_phone", "deposit_screenshot"],
            must_not_say=["已到账", "已完成"],
        ),
        "commands": [],
    }
    state.update(overrides)
    return state


def test_final_reply_payload_includes_language_contract_fields():
    provider = FakeFinalReplyProvider(
        {
            "text": "Please provide your username or registered phone number and proof of payment.",
            "language": "en",
            "tone": "polite",
            "confidence": 0.91,
            "safety_flags": [],
            "reason": "uses language contract",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_reply_result"]["status"] == "accepted"
    payload = provider.calls[0]
    assert payload["detected_language"] == "en"
    assert payload["conversation_language"] == "en"
    assert payload["reply_language"] == "en"
    assert payload["language_result"]["reply_language"] == "en"
    assert payload["supported_languages"] == ["zh-Hans", "en", "tl", "th"]
    assert payload["tenant_persona"]["default_language"] == "zh-Hans"


def test_final_reply_language_mismatch_falls_back():
    provider = FakeFinalReplyProvider(
        {
            "text": "請提供用戶名或註冊手機號，並上傳存款付款截圖。",
            "language": "zh-Hant",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "reason": "wrong language",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "fallback"
    assert "language_mismatch" in result["final_reply_result"]["violations"]


def test_english_reply_does_not_fail_because_chinese_must_say_is_translated():
    provider = FakeFinalReplyProvider(
        {
            "text": "Please provide your username or registered phone number and proof of payment.",
            "language": "en",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "semantic match",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "Please provide your username or registered phone number and proof of payment."
    assert result["final_reply_result"]["status"] == "accepted"


def test_tagalog_reply_does_not_fail_because_chinese_must_say_is_translated():
    provider = FakeFinalReplyProvider(
        {
            "text": "Pakibigay ang iyong username o rehistradong numero at proof of payment.",
            "language": "tl",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "semantic match",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state(reply_language="tl", conversation_language="tl", detected_language="tl")))

    assert result["final_response_text"] == "Pakibigay ang iyong username o rehistradong numero at proof of payment."
    assert result["final_reply_result"]["status"] == "accepted"


def test_thai_reply_passes_deposit_screenshot_semantic_check():
    provider = FakeFinalReplyProvider(
        {
            "text": "กรุณาส่งชื่อผู้ใช้หรือเบอร์โทรที่ลงทะเบียน และหลักฐานการชำระเงิน",
            "language": "th",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "semantic match",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state(reply_language="th", conversation_language="th", detected_language="th")))

    assert result["final_reply_result"]["status"] == "accepted"


def test_final_reply_disabled_keeps_fallback_behavior():
    provider = FakeFinalReplyProvider(
        {
            "text": "Please provide your username or registered phone number and proof of payment.",
            "language": "en",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "not used",
        }
    )
    service = FinalReplyService(provider=provider, enabled=False)

    result = asyncio.run(service.compose(base_state()))

    assert provider.calls == []
    assert result["final_response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["fallback_reason"] == "disabled"
