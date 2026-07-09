import asyncio

from app.graph.builder import build_workflow_graph


class FakeRewriteService:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.payloads = []

    async def rewrite(self, payload: dict) -> dict:
        self.calls.append("rewrite_llm")
        self.payloads.append(payload)
        return {
            "rewritten_question": "User says deposit did not arrive",
            "normalized_query": "deposit did not arrive",
            "detected_language": "en",
            "language": "en",
            "language_confidence": 0.93,
            "preserved_entities": [],
            "missing_or_ambiguous": [],
            "risk_flags": [],
            "confidence": 0.91,
            "reason": "rewritten by fake LLM",
            "provider": "fake",
            "mode": "rewrite_authoritative",
        }


class FakeIntentService:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.payloads = []

    async def route(self, payload: dict) -> dict:
        self.calls.append("router_llm")
        self.payloads.append(payload)
        return {
            "intent": "deposit_missing",
            "route": "sop",
            "confidence": 0.94,
            "sop_name": "deposit_missing",
            "faq_query": None,
            "risk_level": "elevated",
            "requires_human": False,
            "requires_backend": True,
            "missing_slots": ["account_or_phone", "deposit_screenshot"],
            "preserved_entities": [],
            "reason": "fake router selected SOP",
            "provider": "fake",
            "mode": "guarded_authoritative",
        }


class FakeSopSlotService:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.payloads = []

    async def extract_sop_slots(self, payload: dict) -> dict:
        self.calls.append("sop_slot_llm")
        self.payloads.append(payload)
        return {
            "intent": "deposit_missing",
            "extracted_slots": {},
            "attachment_classification": {},
            "missing_slots": ["account_or_phone", "deposit_screenshot"],
            "confidence": {},
            "reason": "fake slot extractor ran",
            "provider": "fake",
            "mode": "sop_slot",
        }


class FakeFinalReplyService:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.payloads = []

    async def compose(self, state: dict) -> dict:
        self.calls.append("final_reply_llm")
        self.payloads.append(state)
        return {
            **state,
            "response_text_fallback": state["response_text_fallback"],
            "final_response_text": "Please send your account or phone number along with the deposit screenshot so I can help check this for you.",
            "final_reply_result": {"status": "accepted", "confidence": 0.95},
        }


def test_llm_mode_runs_llms_inside_graph_in_order_and_uses_final_text():
    calls: list[str] = []
    rewrite_service = FakeRewriteService(calls)
    intent_service = FakeIntentService(calls)
    sop_slot_service = FakeSopSlotService(calls)
    final_reply_service = FakeFinalReplyService(calls)
    graph = build_workflow_graph(
        llm_rewrite_service=rewrite_service,
        llm_intent_service=intent_service,
        llm_sop_slot_service=sop_slot_service,
        final_reply_service=final_reply_service,
        llm_sop_slot_enabled=True,
        llm_final_reply_enabled=True,
        tenant_persona_default_language="zh-Hans",
        tenant_supported_languages=["zh-Hans", "en", "es", "tl"],
    )

    result = asyncio.run(
        graph.ainvoke(
            {
            "tenant_id": "default",
            "channel_type": "livechat",
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "raw_user_input": "my deposit did not arrive",
            "event_type": "MESSAGE_CREATED",
            "attachments": [],
            "slot_memory": {},
            "recent_messages": [],
            "commands": [],
            "errors": [],
            }
        )
    )

    assert calls == ["rewrite_llm", "router_llm", "sop_slot_llm", "final_reply_llm"]
    assert intent_service.payloads[0]["rewritten_question"] == "User says deposit did not arrive"
    assert intent_service.payloads[0]["reply_language"] == "en"
    assert sop_slot_service.payloads[0]["intent"] == "deposit_missing"
    assert result["reply_language"] == "en"
    assert result["detected_language"] == "en"
    assert result["route"] == "sop"
    assert result["rewrite_source"] == "llm_rewrite_authoritative"
    assert result["route_source"] == "llm_guarded_authoritative"
    assert result["sop_slot_source"] == "llm_guarded"
    assert result["final_reply_result"]["status"] == "accepted"
    assert result["intent_result"]["intent"] == "deposit_missing"
    assert result["slot_memory"]["last_user_language"] == "en"
    assert result["slot_memory"]["last_reply_language"] == "en"
    assert result["final_response_text"].startswith("Please send your account")
    assert [str(command["type"]) for command in result["commands"]] == ["livechat.send_image", "livechat.send_text"]
    assert result["commands"][0]["payload"]["asset_key"] == "deposit_payment_success_example"
    assert result["commands"][1]["payload"]["final_reply_target"] is True
    assert result["commands"][1]["payload"]["text"] == result["final_response_text"]
    assert result.get("route_locked") is not True
