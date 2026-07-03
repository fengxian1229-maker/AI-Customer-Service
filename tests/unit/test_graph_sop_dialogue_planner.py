import asyncio

from app.graph.builder import build_workflow_graph
from app.graph.nodes import make_sop_node


def _sop_state(**overrides):
    state = {
        "tenant_id": "default",
        "conversation_id": "livechat:chat-1",
        "channel_type": "livechat",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "raw_user_input": "我的存款没到账，电话 13800138000",
        "rewritten_question": "我的存款没到账，电话 13800138000",
        "reply_language": "zh-Hans",
        "route": "sop",
        "intent_result": {"intent": "deposit_missing", "route": "sop"},
        "workflow_stage": None,
        "slot_memory": {},
        "attachments": [{"url": "https://cdn.example/receipt.png", "name": "receipt.png"}],
        "recent_messages": [],
        "commands": [],
        "errors": [],
    }
    state.update(overrides)
    return state


class PlannerOnlyService:
    def __init__(self, result: dict | None = None, legacy_result: dict | None = None) -> None:
        self.plan_calls = []
        self.extract_calls = []
        self.result = result or {
            "intent_relation": "current_sop_supplement",
            "slot_updates": {"phone": "13800138000"},
            "slot_confidence": {"phone": 0.97},
            "missing_slots": ["receipt_screenshot"],
            "should_ask_confirmation": False,
            "reply_draft": "",
            "reason": "phone supplied",
            "provider": "fake",
            "mode": "sop_dialogue_planner",
        }
        self.legacy_result = legacy_result or {
            "intent": "deposit_missing",
            "extracted_slots": {"account_or_phone": "legacy"},
            "attachment_classification": {},
            "missing_slots": [],
            "confidence": {"account_or_phone": 0.9},
            "reason": "legacy extractor should not run",
        }

    async def plan_sop_dialogue(self, payload: dict) -> dict:
        self.plan_calls.append(payload)
        return self.result

    async def extract_sop_slots(self, payload: dict) -> dict:
        self.extract_calls.append(payload)
        return self.legacy_result


def test_sop_node_prefers_dialogue_planner_over_slot_extractor():
    service = PlannerOnlyService()
    node = make_sop_node(service, llm_sop_slot_enabled=True)

    result = asyncio.run(node(_sop_state()))

    assert len(service.plan_calls) == 1
    assert service.extract_calls == []
    assert service.plan_calls[0]["sop_name"] == "deposit_missing"
    assert result["sop_slot_source"] == "llm_dialogue_planner"
    assert result["llm_sop_dialogue_plan"]["status"] == "accepted"
    assert result["llm_sop_slot_result"]["status"] == "accepted"
    assert result["slot_memory"]["phone"] == "13800138000"
    assert result["slot_memory"]["account_or_phone"] == "13800138000"


def test_sop_node_drops_unknown_and_protected_planner_slots():
    service = PlannerOnlyService(
        {
            "intent_relation": "current_sop_supplement",
            "slot_updates": {
                "phone": "13800138000",
                "unknown_slot": "x",
                "telegram_message_id": "999",
            },
            "slot_confidence": {"phone": 0.97, "unknown_slot": 0.99, "telegram_message_id": 0.99},
            "missing_slots": ["receipt_screenshot"],
            "should_ask_confirmation": False,
            "reply_draft": "",
            "reason": "mixed slots",
        }
    )
    node = make_sop_node(service, llm_sop_slot_enabled=True)

    result = asyncio.run(node(_sop_state(slot_memory={"telegram_message_id": "123"})))

    assert result["slot_memory"]["phone"] == "13800138000"
    assert result["slot_memory"]["telegram_message_id"] == "123"
    assert "unknown_slot" not in result["slot_memory"]
    assert set(result["llm_sop_dialogue_plan"]["dropped_slots"]) == {"unknown_slot", "telegram_message_id"}


def test_sop_node_marks_matching_image_analysis_receipt_verified_for_planner():
    service = PlannerOnlyService(
        {
            "intent_relation": "current_sop_supplement",
            "slot_updates": {"phone": "13800138000"},
            "slot_confidence": {"phone": 0.97},
            "missing_slots": ["receipt_screenshot"],
            "should_ask_confirmation": False,
            "reply_draft": "",
            "reason": "phone supplied",
        }
    )
    node = make_sop_node(service, llm_sop_slot_enabled=True)

    result = asyncio.run(
        node(
            _sop_state(
                attachments=[
                    {
                        "url": "https://cdn.example/deposit.png",
                        "content_type": "image/png",
                        "image_analysis_status": "analyzed",
                        "image_analysis": {
                            "is_receipt_like": True,
                            "receipt_kind": "deposit",
                            "confidence": 0.91,
                        },
                    }
                ]
            )
        )
    )

    attachment_summary = service.plan_calls[0]["attachments_summary"][0]
    assert attachment_summary["verified_receipt_attachment"] is True
    assert attachment_summary["receipt_kind"] == "deposit"
    assert result["slot_memory"]["deposit_screenshot"] == "https://cdn.example/deposit.png"


def test_sop_node_falls_back_when_planner_confidence_is_low():
    service = PlannerOnlyService(
        {
            "intent_relation": "current_sop_supplement",
            "slot_updates": {"phone": "13800138000"},
            "slot_confidence": {"phone": 0.2},
            "missing_slots": ["receipt_screenshot"],
            "should_ask_confirmation": False,
            "reply_draft": "",
            "reason": "low confidence",
        }
    )
    node = make_sop_node(service, llm_sop_slot_enabled=True, llm_sop_slot_min_confidence=0.7)

    result = asyncio.run(node(_sop_state()))

    assert len(service.plan_calls) == 1
    assert len(service.extract_calls) == 1
    assert result["llm_sop_dialogue_plan"]["status"] == "fallback"
    assert result["llm_sop_dialogue_plan"]["fallback_reason"] == "low_confidence"
    assert result["sop_slot_source"] == "deterministic"
    assert result["llm_sop_slot_result"]["status"] == "fallback"


def test_sop_node_uses_legacy_extractor_when_planner_falls_back():
    service = PlannerOnlyService(
        {
            "intent_relation": "current_sop_supplement",
            "slot_updates": {"phone": "13800138000"},
            "slot_confidence": {"phone": 0.2},
            "missing_slots": ["receipt_screenshot"],
            "should_ask_confirmation": False,
            "reply_draft": "",
            "reason": "low confidence",
        },
        legacy_result={
            "intent": "deposit_missing",
            "extracted_slots": {"account_or_phone": "13800138000"},
            "attachment_classification": {},
            "missing_slots": ["deposit_screenshot"],
            "confidence": {"account_or_phone": 0.97},
            "reason": "legacy accepted",
            "provider": "fake",
            "mode": "sop_slot",
        },
    )
    node = make_sop_node(service, llm_sop_slot_enabled=True, llm_sop_slot_min_confidence=0.7)

    result = asyncio.run(node(_sop_state()))

    assert len(service.plan_calls) == 1
    assert len(service.extract_calls) == 1
    assert result["llm_sop_dialogue_plan"]["status"] == "accepted"
    assert result["llm_sop_dialogue_plan"]["source"] == "llm_sop_dialogue_planner"
    assert result["llm_sop_dialogue_plan"]["fallback_reason"] == "low_confidence"
    assert result["llm_sop_slot_result"]["status"] == "accepted"
    assert result["slot_memory"]["account_or_phone"] == "13800138000"


def test_sop_node_does_not_call_planner_when_llm_sop_slots_disabled():
    service = PlannerOnlyService()
    node = make_sop_node(service, llm_sop_slot_enabled=False)

    result = asyncio.run(node(_sop_state()))

    assert service.plan_calls == []
    assert service.extract_calls == []
    assert result.get("llm_sop_dialogue_plan", {}).get("source") != "llm_sop_dialogue_planner"
    assert result.get("sop_slot_source") != "llm_dialogue_planner"


def test_enabled_graph_includes_dialogue_plan_in_state():
    service = PlannerOnlyService()
    graph = build_workflow_graph(llm_sop_slot_service=service, llm_sop_slot_enabled=True)

    result = asyncio.run(graph.ainvoke(_sop_state(intent_result=None, route=None)))

    assert len(service.plan_calls) == 1
    assert result["llm_sop_dialogue_plan"]["status"] == "accepted"
    assert result["sop_slot_source"] == "llm_dialogue_planner"
