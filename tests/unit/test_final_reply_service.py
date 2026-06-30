import asyncio

from app.services.final_reply_service import FinalReplyService


class FakeFinalReplyProvider:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {
            "text": "您好，请提供用户名或注册手机号，并上传存款付款截图。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.91,
            "safety_flags": [],
            "reason": "polished fallback",
        }
        self.error = error
        self.calls = []

    async def compose_final_reply(self, payload: dict) -> dict:
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.result


def base_state(**overrides):
    state = {
        "tenant_id": "default",
        "channel_type": "livechat",
        "conversation_id": "livechat:chat-1",
        "raw_user_input": "mi deposito no llegó",
        "rewritten_question": "mi deposito no llegó",
        "recent_messages": [{"sender_role": "customer", "text_content": "hola"}],
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
        "reply_plan": {
            "kind": "ask_missing_slots",
            "must_say": ["用户名或注册手机号", "存款付款截图"],
            "must_not_say": ["已到账", "已完成"],
            "missing_slots": ["account_or_phone", "deposit_screenshot"],
            "allowed_facts": ["需要客户补充资料"],
        },
        "commands": [],
    }
    state.update(overrides)
    return state


def test_final_reply_service_uses_llm_text_when_guardrails_pass():
    provider = FakeFinalReplyProvider()
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "您好，请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "accepted"
    assert provider.calls[0]["tenant_persona"]["default_language"] == "zh"


def test_final_reply_service_falls_back_when_provider_raises():
    provider = FakeFinalReplyProvider(error=RuntimeError("model down"))
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "fallback"
    assert result["final_reply_result"]["fallback_reason"] == "exception"


def test_final_reply_service_falls_back_when_output_adds_unverified_credited_fact():
    provider = FakeFinalReplyProvider(
        {
            "text": "您好，您的款项已到账，请放心。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.92,
            "safety_flags": [],
            "reason": "bad fact",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "fallback"
    assert result["final_reply_result"]["fallback_reason"] == "guardrail_failed"
    assert "forbidden_backend_fact" in result["final_reply_result"]["violations"]


def test_final_reply_service_falls_back_when_ask_missing_slots_omits_account():
    provider = FakeFinalReplyProvider(
        {
            "text": "请上传存款付款截图。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "reason": "missed slot",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert "missing_required_phrase" in result["final_reply_result"]["violations"]
    assert "missing_slot_account_or_phone" in result["final_reply_result"]["violations"]


def test_final_reply_service_human_handoff_disallows_claiming_agent_connected():
    provider = FakeFinalReplyProvider(
        {
            "text": "真人客服已接入，会马上处理。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.95,
            "safety_flags": [],
            "reason": "bad handoff promise",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)
    state = base_state(
        route="human_handoff",
        intent_result={"intent": "explicit_human_request", "route": "human_handoff"},
        active_workflow="human_handoff",
        workflow_stage="handoff_requested",
        response_text="我会为你转接真人客服继续协助。",
        response_text_fallback="我会为你转接真人客服继续协助。",
        reply_plan={
            "kind": "human_handoff",
            "must_say": ["转接真人客服"],
            "must_not_say": ["已接入", "马上处理"],
            "allowed_facts": ["客户请求真人客服", "系统将提出转接请求"],
        },
    )

    result = asyncio.run(service.compose(state))

    assert result["final_response_text"] == "我会为你转接真人客服继续协助。"
    assert "forbidden_phrase" in result["final_reply_result"]["violations"]


def test_final_reply_service_faq_cannot_add_policy_not_in_reply_plan_or_rag():
    provider = FakeFinalReplyProvider(
        {
            "text": "存款请按照页面提示操作，手续费全免。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "added policy",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)
    state = base_state(
        route="faq",
        intent_result={"intent": "deposit_howto", "route": "faq"},
        active_workflow=None,
        workflow_stage=None,
        rag_result={"matched": True, "answer": "存款请按照页面提示操作。"},
        response_text="存款请按照页面提示操作。",
        response_text_fallback="存款请按照页面提示操作。",
        reply_plan={
            "kind": "faq_answer",
            "must_say": ["存款请按照页面提示操作"],
            "must_not_say": ["手续费全免", "保证到账"],
            "allowed_facts": ["存款请按照页面提示操作"],
        },
    )

    result = asyncio.run(service.compose(state))

    assert result["final_response_text"] == "存款请按照页面提示操作。"
    assert "forbidden_phrase" in result["final_reply_result"]["violations"]
