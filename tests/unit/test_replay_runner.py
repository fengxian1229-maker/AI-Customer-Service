import json
import asyncio
from pathlib import Path

from app.graph.builder import build_workflow_graph
from app.graph.nodes import build_graph_state_from_event
from app.schemas.events import InboundEvent
from app.workflows.command_contracts import CommandType


EXTERNAL_COMMAND_TYPES = {
    str(CommandType.TELEGRAM_SEND_CASE_CARD),
    str(CommandType.TELEGRAM_APPEND_TO_CASE),
    str(CommandType.BACKEND_QUERY),
    str(CommandType.PENDING_REPLY_LOOKUP),
    str(CommandType.HUMAN_HANDOFF_REQUESTED),
    str(CommandType.RAG_PLACEHOLDER),
}


def test_replay_runner_executes_all_fixtures():
    fixture_dir = Path("tests/fixtures/replay")
    graph = build_workflow_graph()

    for path in sorted(fixture_dir.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        result = run_replay_case(graph, path.stem, fixture["input_messages"])

        assert result["intent"] == fixture["expected_intent"], path.name
        assert result["active_workflow"] == fixture["expected_active_workflow"], path.name
        assert result["workflow_stage"] == fixture["expected_workflow_stage"], path.name
        assert result["required_slots"] == fixture["expected_required_slots"], path.name
        assert result["outbound_command_types"] == fixture["expected_outbound_command_types"], path.name
        assert result["external_command_types"] == fixture["expected_external_command_types"], path.name


def run_replay_case(graph, case_name: str, input_messages: list[str]) -> dict:
    conversation = initial_conversation(case_name)
    all_command_types = []
    final_state = None

    for index, message in enumerate(input_messages, start=1):
        event = make_event(case_name, index, message)
        state = build_graph_state_from_event(event, conversation)
        final_state = asyncio.run(graph.ainvoke(state))
        conversation.update(
            {
                "status": final_state.get("status") or conversation.get("status"),
                "active_workflow": final_state.get("active_workflow"),
                "workflow_stage": final_state.get("workflow_stage"),
                "slot_memory": final_state.get("slot_memory") or {},
            }
        )
        all_command_types.extend(str(command["type"]) for command in final_state.get("commands", []))

    outbound_command_types = unique_preserving_order(
        command_type for command_type in all_command_types if command_type == str(CommandType.LIVECHAT_SEND_TEXT)
    )
    external_command_types = unique_preserving_order(
        command_type for command_type in all_command_types if command_type in EXTERNAL_COMMAND_TYPES
    )

    return {
        "intent": (final_state.get("intent_result") or {}).get("intent"),
        "active_workflow": final_state.get("active_workflow"),
        "workflow_stage": final_state.get("workflow_stage"),
        "required_slots": required_slots(final_state),
        "outbound_command_types": outbound_command_types,
        "external_command_types": external_command_types,
    }


def initial_conversation(case_name: str) -> dict:
    conversation = {
        "conversation_id": "livechat:replay-chat",
        "tenant_id": "default",
        "channel_type": "livechat",
        "chat_id": "replay-chat",
        "current_thread_id": "replay-thread",
        "status": "AI_ACTIVE",
        "active_workflow": None,
        "workflow_stage": None,
        "slot_memory": {},
    }
    if case_name == "waiting_backend_supplement":
        conversation.update(
            {
                "status": "WAITING_EXTERNAL",
                "active_workflow": "deposit_missing",
                "workflow_stage": "waiting_backend",
                "slot_memory": {
                    "account_or_phone": "user123",
                    "deposit_screenshot": "https://example.test/old.jpg",
                    "telegram_case_id": "tg:900001",
                    "telegram_message_id": 900001,
                },
            }
        )
    return conversation


def make_event(case_name: str, index: int, message: str) -> InboundEvent:
    standard_event_type = "FILE_RECEIVED" if case_name == "waiting_backend_supplement" else "MESSAGE_CREATED"
    payload = {"event": {"text": message}, "text": message}
    if standard_event_type == "FILE_RECEIVED":
        payload["event"]["file"] = {"url": "https://example.test/supplement.jpg", "name": "supplement.jpg"}
    return InboundEvent(
        source="replay_fixture",
        raw_action="replay.message",
        chat_id="replay-chat",
        thread_id="replay-thread",
        event_id=f"{case_name}-{index}",
        event_type="message",
        standard_event_type=standard_event_type,
        author_id="customer",
        sender_role="external",
        occurred_at="2026-06-24 00:00:00.000000",
        dedup_key=f"replay:{case_name}:{index}",
        payload_json=payload,
        ignored=False,
    )


def required_slots(state: dict) -> list[str]:
    intent = (state.get("intent_result") or {}).get("intent")
    slot_memory = state.get("slot_memory") or {}
    if state.get("workflow_stage") == "collecting_slots":
        required_by_intent = {
            "deposit_missing": ["account_or_phone", "deposit_screenshot"],
            "withdrawal_missing": ["account_or_phone", "withdrawal_screenshot"],
            "withdrawal_blocked_or_rollover": ["account_or_phone"],
            "pending_reply_lookup": ["pending_reply_identity"],
        }
        return [slot for slot in required_by_intent.get(intent, []) if not slot_memory.get(slot)]
    if state.get("workflow_stage") == "waiting_backend":
        return [slot for slot in ["forwarded_attachment_urls"] if slot_memory.get(slot)]
    return []


def unique_preserving_order(items) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
