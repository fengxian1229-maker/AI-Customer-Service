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


def test_final_reply_prompt_requires_contextual_answer_planning():
    from app.llm.final_reply_provider import FINAL_REPLY_SYSTEM_PROMPT

    assert "understand the customer's current question" in FINAL_REPLY_SYSTEM_PROMPT
    assert "recent_messages" in FINAL_REPLY_SYSTEM_PROMPT
    assert "backend_result" in FINAL_REPLY_SYSTEM_PROMPT
    assert "fallback text is a safe draft/fact source" in FINAL_REPLY_SYSTEM_PROMPT
    assert "avoiding mechanical copy-through" in FINAL_REPLY_SYSTEM_PROMPT
    assert "answer that value directly" in FINAL_REPLY_SYSTEM_PROMPT
    assert "node_facts" in FINAL_REPLY_SYSTEM_PROMPT
    assert "reply_plan.allowed_facts" in FINAL_REPLY_SYSTEM_PROMPT
    assert "Do not use internal organization or system labels" in FINAL_REPLY_SYSTEM_PROMPT
    assert "后台" in FINAL_REPLY_SYSTEM_PROMPT
    assert "backend" in FINAL_REPLY_SYSTEM_PROMPT


def test_final_reply_prompt_includes_lingxi_persona_and_compliance_bounds():
    from app.llm.final_reply_provider import FINAL_REPLY_SYSTEM_PROMPT

    assert "客服灵犀" in FINAL_REPLY_SYSTEM_PROMPT
    assert 'not a phrase to repeat in every answer' in FINAL_REPLY_SYSTEM_PROMPT
    assert 'Do not introduce yourself again with phrases like "我是客服灵犀"' in FINAL_REPLY_SYSTEM_PROMPT
    assert "avoid repeating the same opening phrase" in FINAL_REPLY_SYSTEM_PROMPT
    assert "感谢您的谅解" in FINAL_REPLY_SYSTEM_PROMPT
    assert "official intelligent customer service assistant for a gaming platform" in FINAL_REPLY_SYSTEM_PROMPT
    assert "professional, patient, restrained, trustworthy" in FINAL_REPLY_SYSTEM_PROMPT
    assert "Never invent backend facts" in FINAL_REPLY_SYSTEM_PROMPT
    assert "Never promise crediting, withdrawal approval, profit, loss recovery" in FINAL_REPLY_SYSTEM_PROMPT
    assert "Never encourage deposits, betting, chasing losses" in FINAL_REPLY_SYSTEM_PROMPT
    assert "Never ask for passwords, verification codes" in FINAL_REPLY_SYSTEM_PROMPT


def test_final_reply_service_payload_includes_node_template_and_facts():
    provider = FakeFinalReplyProvider()
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                node_reply_template="sop_missing_slots",
                node_facts={
                    "sop_name": "deposit_missing",
                    "missing_slots": ["account_or_phone", "deposit_screenshot"],
                },
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted"
    assert provider.calls[0]["node_reply_template"] == "sop_missing_slots"
    assert "Node reply template: SOP missing slots" in provider.calls[0]["node_reply_instruction"]
    assert provider.calls[0]["node_facts"]["sop_name"] == "deposit_missing"
    assert provider.calls[0]["recent_messages"] == [{"sender_role": "customer", "text_content": "hola"}]


def test_final_reply_service_derives_faq_node_facts_from_rag_result():
    provider = FakeFinalReplyProvider(
        {
            "text": "你可以在提款页面按提示提交提款申请。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.91,
            "safety_flags": [],
            "reason": "faq answer",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                route="faq",
                intent_result={"intent": "withdrawal_howto", "route": "faq", "faq_intent": "withdrawal_howto"},
                rag_result={
                    "matched": True,
                    "answer": "你可以在提款页面按提示提交提款申请。",
                    "source": "knowledge_documents",
                    "query": "怎么提款",
                    "documents": [{"title": "提款方式说明"}],
                },
                response_text="你可以在提款页面按提示提交提款申请。",
                response_text_fallback="你可以在提款页面按提示提交提款申请。",
                reply_plan={
                    "kind": "faq_answer",
                    "fallback_text": "你可以在提款页面按提示提交提款申请。",
                    "must_say": ["你可以在提款页面按提示提交提款申请"],
                    "must_not_say": ["手续费全免"],
                    "allowed_facts": ["你可以在提款页面按提示提交提款申请。"],
                },
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted"
    assert provider.calls[0]["node_reply_template"] == "faq_answer"
    assert provider.calls[0]["node_facts"]["faq"]["answer"] == "你可以在提款页面按提示提交提款申请。"


def test_final_reply_service_uses_llm_text_when_guardrails_pass():
    provider = FakeFinalReplyProvider()
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "您好，请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "accepted"
    assert provider.calls[0]["tenant_persona"]["default_language"] == "es"


def test_final_reply_service_audits_internal_staff_label_for_customer_reply():
    provider = FakeFinalReplyProvider(
        {
            "text": "关于您的充值问题，后台工作人员回复显示目前查询未到账。我们会持续跟进处理。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.91,
            "safety_flags": [],
            "used_facts": ["目前查询未到账"],
            "reason": "staff update",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                raw_user_input="目前查询未到账",
                rewritten_question="目前查询未到账",
                workflow_stage="backend_replied",
                response_text="后台回复显示目前查询未到账。我们会持续跟进处理。",
                response_text_fallback="后台回复显示目前查询未到账。我们会持续跟进处理。",
                node_reply_template="telegram_staff_reply",
                reply_language="zh-Hans",
                reply_plan={
                    "kind": "telegram_staff_reply",
                    "fallback_text": "后台回复显示目前查询未到账。我们会持续跟进处理。",
                    "allowed_facts": ["目前查询未到账"],
                    "must_not_say": ["后台工作人员", "工作人员", "转交后台"],
                },
            )
        )
    )

    assert result["final_response_text"] == "关于您的充值问题，后台工作人员回复显示目前查询未到账。我们会持续跟进处理。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "internal_organization_label" in result["final_reply_result"]["violations"]


def test_final_reply_service_rejects_standalone_backend_label_for_customer_reply():
    provider = FakeFinalReplyProvider(
        {
            "text": "后台回复你的存款已处理完成，请你确认账户余额。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.91,
            "safety_flags": [],
            "used_facts": ["存款已处理完成"],
            "reason": "staff update",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                raw_user_input="存款已处理完成",
                rewritten_question="存款已处理完成",
                workflow_stage="backend_replied",
                response_text="已为您核实到这笔存款已处理完成，请您确认账户余额。",
                response_text_fallback="已为您核实到这笔存款已处理完成，请您确认账户余额。",
                node_reply_template="telegram_staff_reply",
                reply_language="zh-Hans",
                reply_plan={
                    "kind": "telegram_staff_reply",
                    "fallback_text": "已为您核实到这笔存款已处理完成，请您确认账户余额。",
                    "allowed_facts": ["存款已处理完成"],
                    "must_not_say": ["后台", "後台", "backend", "保证", "一定"],
                },
            )
        )
    )

    assert result["final_response_text"] == "后台回复你的存款已处理完成，请你确认账户余额。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "internal_organization_label" in result["final_reply_result"]["violations"]


def test_final_reply_service_payload_preserves_backend_context():
    provider = FakeFinalReplyProvider(
        {
            "text": "是的，剩余流水约为 1375.09。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "reason": "direct answer to current follow-up using backend fact",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)
    recent_messages = [
        {"sender_role": "assistant", "text_content": "剩余流水约为 1375.09。"},
        {"sender_role": "customer", "text_content": "刚刚是说我还有多少流水？"},
    ]
    backend_result = {
        "answer": "后台查询显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。",
        "raw_user_input": "刚刚是说我还有多少流水？",
        "rewritten_question": "刚才提到的账户 3239413629 的剩余流水金额是多少？",
        "query": {"remaining_turnover": 1375.09, "player_found": True, "active_requirements_count": 2},
    }

    result = asyncio.run(
        service.compose(
            base_state(
                raw_user_input="刚刚是说我还有多少流水？",
                rewritten_question="刚才提到的账户 3239413629 的剩余流水金额是多少？",
                recent_messages=recent_messages,
                response_text="后台查询显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。请先完成对应流水后再尝试提款。",
                response_text_fallback="后台查询显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。请先完成对应流水后再尝试提款。",
                backend_result=backend_result,
                reply_plan={
                    "kind": "backend_query_result",
                    "fallback_text": "后台查询显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。请先完成对应流水后再尝试提款。",
                    "must_say_exact": ["1375.09"],
                    "must_not_say": ["已到账", "已完成", "保证"],
                    "allowed_facts": [
                        "后台查询显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。",
                        "remaining_turnover=1375.09",
                    ],
                },
            )
        )
    )

    assert result["final_response_text"] == "是的，剩余流水约为 1375.09。"
    assert result["final_reply_result"]["status"] == "accepted"
    assert provider.calls[0]["recent_messages"] == recent_messages
    assert provider.calls[0]["backend_result"] == backend_result
    assert provider.calls[0]["raw_user_input"] == "刚刚是说我还有多少流水？"
    assert provider.calls[0]["rewritten_question"] == "刚才提到的账户 3239413629 的剩余流水金额是多少？"


def test_final_reply_service_accepts_used_facts_from_backend_result():
    provider = FakeFinalReplyProvider(
        {
            "text": "查询结果显示剩余流水约为 1375.09。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "used_facts": ["remaining_turnover=1375.09"],
            "reason": "used verified backend number",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                response_text="查询结果显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。",
                response_text_fallback="查询结果显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。",
                backend_result={"answer": "查询完成", "query": {"remaining_turnover": 1375.09}},
                reply_plan={
                    "kind": "backend_query_result",
                    "fallback_text": "查询结果显示当前可能仍有未完成流水要求，剩余流水约为 1375.09。",
                    "must_say_exact": ["1375.09"],
                    "allowed_facts": ["remaining_turnover=1375.09"],
                },
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted"
    assert result["final_reply_result"]["used_facts"] == ["remaining_turnover=1375.09"]


def test_final_reply_service_allows_low_risk_capability_used_facts():
    provider = FakeFinalReplyProvider(
        {
            "text": "你好，我可以协助处理存款、提款、流水要求、截图凭证、账号访问问题，也可以帮你请求真人客服。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "used_facts": ["可以咨询存款、提款、流水、截图、账号访问或真人客服"],
            "reason": "capability answer",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                raw_user_input="你还能做什么？",
                rewritten_question="你还能做什么？",
                route="final_reply",
                intent_result={"intent": "casual_chat", "route": "final_reply"},
                active_workflow=None,
                workflow_stage=None,
                response_text="你好，我可以协助处理存款、提款、流水要求、截图凭证、账号访问问题，也可以帮你请求真人客服。",
                response_text_fallback="你好，我可以协助处理存款、提款、流水要求、截图凭证、账号访问问题，也可以帮你请求真人客服。",
                node_facts={
                    "supported_topics": ["存款", "提款", "流水", "截图", "账号访问", "真人客服"],
                    "allowed_facts": ["支持存款问题", "支持提款问题", "支持流水要求查询", "支持请求真人客服"],
                },
                reply_plan={
                    "kind": "casual_chat",
                    "fallback_text": "你好，我可以协助处理存款、提款、流水要求、截图凭证、账号访问问题，也可以帮你请求真人客服。",
                    "allowed_facts": ["支持存款问题", "支持提款问题", "支持流水要求查询", "支持请求真人客服"],
                },
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted"
    assert result["final_response_text"].startswith("你好，我可以协助处理存款")


def test_final_reply_service_still_rejects_backend_fact_in_low_risk_used_facts():
    provider = FakeFinalReplyProvider(
        {
            "text": "你好，我可以继续协助你确认问题。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "used_facts": ["款项已到账"],
            "reason": "bad metadata fact",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                route="final_reply",
                intent_result={"intent": "casual_chat", "route": "final_reply"},
                active_workflow=None,
                workflow_stage=None,
                response_text="你好，我可以继续协助你确认问题。",
                response_text_fallback="你好，我可以继续协助你确认问题。",
                reply_plan={"kind": "casual_chat", "fallback_text": "你好，我可以继续协助你确认问题。"},
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "forbidden_backend_fact" in result["final_reply_result"]["violations"]


def test_final_reply_service_rejects_unverified_time_commitment_in_used_facts():
    provider = FakeFinalReplyProvider(
        {
            "text": "我会为你查询，24小时内处理。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "used_facts": ["24小时内处理"],
            "reason": "invented time",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                response_text="已收到你的账号资料，我会为你查询提款限制或流水要求，请稍等。",
                response_text_fallback="已收到你的账号资料，我会为你查询提款限制或流水要求，请稍等。",
                reply_plan={
                    "kind": "backend_waiting",
                    "fallback_text": "已收到你的账号资料，我会为你查询提款限制或流水要求，请稍等。",
                    "allowed_facts": ["已收到账号资料", "将查询提款限制或流水要求"],
                },
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "unverified_used_fact" in result["final_reply_result"]["violations"]
    assert "unverified_time_commitment" in result["final_reply_result"]["violations"]


def test_final_reply_service_rejects_backend_sync_claim_without_command_even_in_used_facts():
    provider = FakeFinalReplyProvider(
        {
            "text": "已提交后台，请稍等。",
            "language": "zh-Hans",
            "tone": "polite",
            "confidence": 0.93,
            "safety_flags": [],
            "used_facts": ["已提交后台"],
            "reason": "invented sync",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                response_text="请稍等，我们会继续协助确认。",
                response_text_fallback="请稍等，我们会继续协助确认。",
                reply_plan={
                    "kind": "backend_waiting",
                    "fallback_text": "请稍等，我们会继续协助确认。",
                    "allowed_facts": ["继续协助确认"],
                },
                commands=[],
            )
        )
    )

    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "unverified_backend_sync_claim" in result["final_reply_result"]["violations"]
    assert "unverified_used_fact" in result["final_reply_result"]["violations"]


def test_final_reply_service_falls_back_when_provider_raises():
    provider = FakeFinalReplyProvider(error=RuntimeError("model down"))
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(service.compose(base_state()))

    assert result["final_response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "fallback"
    assert result["final_reply_result"]["fallback_reason"] == "exception"


def test_final_reply_service_provider_exception_uses_english_fallback_when_available():
    provider = FakeFinalReplyProvider(error=RuntimeError("model down"))
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                raw_user_input="Hello, problems with the deposit",
                rewritten_question="Hello, problems with the deposit",
                reply_language="en",
                response_text=(
                    "To help check this deposit, please provide your username or registered phone number "
                    "and upload your own successful payment screenshot."
                ),
                response_text_fallback=(
                    "To help check this deposit, please provide your username or registered phone number "
                    "and upload your own successful payment screenshot."
                ),
            )
        )
    )

    assert result["final_response_text"].startswith("To help check this deposit")
    assert "为了帮你查询" not in result["final_response_text"]
    assert result["final_reply_result"]["status"] == "fallback"
    assert result["final_reply_result"]["fallback_reason"] == "exception"


def test_final_reply_service_low_confidence_is_audited_without_fallback():
    provider = FakeFinalReplyProvider(
        {
            "text": "Please provide your username or registered phone number and upload your payment screenshot.",
            "language": "en",
            "tone": "polite",
            "confidence": 0.2,
            "safety_flags": [],
            "used_facts": [],
            "reason": "low confidence but usable text",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True, min_confidence=0.7)

    result = asyncio.run(
        service.compose(
            base_state(
                reply_language="en",
                missing_slots=["account_or_phone", "deposit_screenshot"],
                response_text="Please provide your username or registered phone number and upload your payment screenshot.",
                response_text_fallback="Please provide your username or registered phone number and upload your payment screenshot.",
                reply_plan={
                    "kind": "ask_missing_slots",
                    "fallback_text": "Please provide your username or registered phone number and upload your payment screenshot.",
                    "missing_slots": ["account_or_phone", "deposit_screenshot"],
                    "allowed_facts": ["Need customer to provide missing details"],
                },
            )
        )
    )

    assert result["final_response_text"].startswith("Please provide your username")
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert result["final_reply_result"]["warning_reason"] == "low_confidence"
    assert "low_confidence" in result["final_reply_result"]["violations"]


def test_final_reply_service_audits_when_output_adds_unverified_credited_fact():
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

    assert result["final_response_text"] == "您好，您的款项已到账，请放心。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert result["final_reply_result"]["warning_reason"] == "guardrail_audit"
    assert "forbidden_backend_fact" in result["final_reply_result"]["violations"]


def test_final_reply_service_allows_credited_fact_from_staff_reply_plan():
    provider = FakeFinalReplyProvider(
        {
            "text": "已为您核实到款项已到账，请刷新页面后确认账户余额。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.92,
            "safety_flags": [],
            "reason": "polished staff reply",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                workflow_stage="backend_replied",
                response_text="已收到处理更新，我们会按照这个更新继续协助您处理。",
                response_text_fallback="已收到处理更新，我们会按照这个更新继续协助您处理。",
                reply_plan={
                    "kind": "telegram_staff_reply",
                    "fallback_text": "已收到处理更新，我们会按照这个更新继续协助您处理。",
                    "allowed_facts": ["已经到账，刷新一下页面看看"],
                    "staff_reply_key_facts": [],
                    "must_not_say": ["保证", "一定"],
                },
            )
        )
    )

    assert result["final_response_text"] == "已为您核实到款项已到账，请刷新页面后确认账户余额。"
    assert result["final_reply_result"]["status"] == "accepted"


def test_final_reply_service_audits_staff_reply_framed_as_customer_feedback():
    provider = FakeFinalReplyProvider(
        {
            "text": "收到您的反馈。关于这笔订单手机号不一致的情况，后台已进行回复，我们将依据此更新继续为您处理。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.92,
            "safety_flags": [],
            "reason": "misframed staff reply",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)

    result = asyncio.run(
        service.compose(
            base_state(
                raw_user_input="我查了这笔订单，貌似手机号不对",
                rewritten_question="我查了这笔订单，貌似手机号不对",
                workflow_stage="waiting_customer_supplement",
                response_text="后台核实时发现手机号可能不一致，请你再次确认并发送正确的注册手机号，我们收到后会继续协助确认。",
                response_text_fallback="后台核实时发现手机号可能不一致，请你再次确认并发送正确的注册手机号，我们收到后会继续协助确认。",
                reply_plan={
                    "kind": "telegram_staff_reply",
                    "fallback_text": "后台核实时发现手机号可能不一致，请你再次确认并发送正确的注册手机号，我们收到后会继续协助确认。",
                    "allowed_facts": ["我查了这笔订单，貌似手机号不对"],
                    "staff_reply_key_facts": [],
                    "must_not_say": ["保证", "一定"],
                },
            )
        )
    )

    assert result["final_response_text"] == "收到您的反馈。关于这笔订单手机号不一致的情况，后台已进行回复，我们将依据此更新继续为您处理。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert result["final_reply_result"]["warning_reason"] == "guardrail_audit"
    assert "staff_reply_framed_as_customer_feedback" in result["final_reply_result"]["violations"]


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

    assert result["final_response_text"] == "请上传存款付款截图。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "missing_required_phrase" in result["final_reply_result"]["violations"]
    assert "missing_slot_account_or_phone" in result["final_reply_result"]["violations"]


def test_final_reply_service_audits_human_handoff_claiming_agent_connected():
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

    assert result["final_response_text"] == "真人客服已接入，会马上处理。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "forbidden_phrase" in result["final_reply_result"]["violations"]


def test_final_reply_service_audits_faq_policy_not_in_reply_plan_or_rag():
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

    assert result["final_response_text"] == "存款请按照页面提示操作，手续费全免。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "forbidden_phrase" in result["final_reply_result"]["violations"]


def test_final_reply_service_audits_internal_telegram_case_id():
    provider = FakeFinalReplyProvider(
        {
            "text": "好的，案件 tg:21 仍在确认中，有更新会通知你。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "leaked internal id",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)
    state = base_state(
        route="final_reply",
        intent_result={"intent": "acknowledgement", "route": "final_reply"},
        active_workflow="withdrawal_missing",
        workflow_stage="waiting_backend",
        response_text="收到，案件仍在确认中，有更新会在这里通知你。",
        response_text_fallback="收到，案件仍在确认中，有更新会在这里通知你。",
        reply_plan={
            "kind": "acknowledgement",
            "fallback_text": "收到，案件仍在确认中，有更新会在这里通知你。",
            "must_not_say": ["已到账", "已完成"],
            "allowed_facts": ["案件仍在确认中"],
        },
    )

    result = asyncio.run(service.compose(state))

    assert result["final_response_text"] == "好的，案件 tg:21 仍在确认中，有更新会通知你。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "internal_telegram_identifier" in result["final_reply_result"]["violations"]


def test_final_reply_service_audits_backend_sync_claim_without_append_command():
    provider = FakeFinalReplyProvider(
        {
            "text": "好的，收到您的更正，已同步给后台继续核实，请稍等。",
            "language": "zh",
            "tone": "polite",
            "confidence": 0.94,
            "safety_flags": [],
            "reason": "unverified sync claim",
        }
    )
    service = FinalReplyService(provider=provider, enabled=True)
    state = base_state(
        route="final_reply",
        intent_result={"intent": "acknowledgement", "route": "final_reply"},
        active_workflow="withdrawal_missing",
        workflow_stage="waiting_backend",
        response_text="收到，案件仍在确认中，有更新会在这里通知你。",
        response_text_fallback="收到，案件仍在确认中，有更新会在这里通知你。",
        reply_plan={
            "kind": "acknowledgement",
            "fallback_text": "收到，案件仍在确认中，有更新会在这里通知你。",
            "must_not_say": ["已到账", "已完成"],
            "allowed_facts": ["案件仍在确认中"],
        },
        commands=[],
    )

    result = asyncio.run(service.compose(state))

    assert result["final_response_text"] == "好的，收到您的更正，已同步给后台继续核实，请稍等。"
    assert result["final_reply_result"]["status"] == "accepted_with_warnings"
    assert "unverified_backend_sync_claim" in result["final_reply_result"]["violations"]


def test_final_reply_fallback_renders_structured_customer_reply_when_text_missing():
    service = FinalReplyService(provider=None, enabled=False)
    state = base_state(
        response_text=None,
        response_text_fallback=None,
        reply_language="es",
        customer_reply={
            "intent": "backend_player_not_found",
            "facts": {},
            "language": "es",
        },
    )

    result = asyncio.run(service.compose(state))

    assert "No encontramos datos de jugador" in result["final_response_text"]
    assert result["final_reply_result"]["fallback_reason"] == "disabled"


def test_final_reply_provider_sends_global_and_node_system_prompts(monkeypatch):
    from app.core.settings import Settings
    from app.llm.final_reply_provider import FinalReplyLLMProvider

    captured = {}

    class FakeStructuredModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return {
                "text": "请提供用户名或注册手机号。",
                "language": "zh-Hans",
                "tone": "polite",
                "confidence": 0.91,
                "safety_flags": [],
                "reason": "uses node template",
            }

    class FakeModel:
        def with_structured_output(self, schema=None, method=None):
            captured["schema"] = schema
            captured["method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr("app.llm.final_reply_provider.build_gemini_chat_model", lambda settings: FakeModel())
    provider = FinalReplyLLMProvider(Settings(livechat_agent_access_token="x", livechat_account_id="y"))

    result = asyncio.run(
        provider.compose_final_reply(
            {
                "raw_user_input": "无法提款",
                "reply_language": "zh-Hans",
                "response_text_fallback": "请提供用户名或注册手机号。",
                "node_reply_template": "sop_missing_slots",
                "node_reply_instruction": "Node reply template: SOP missing slots.",
                "node_facts": {"missing_slots": ["account_or_phone"]},
                "reply_plan": {"kind": "ask_missing_slots"},
            }
        )
    )

    assert result["text"] == "请提供用户名或注册手机号。"
    assert captured["method"] == "json_schema"
    assert captured["messages"][0][0] == "system"
    assert "Final Reply Composer" in captured["messages"][0][1]
    assert captured["messages"][1] == ("system", "Node reply template: SOP missing slots.")
    assert captured["messages"][2][0] == "human"
