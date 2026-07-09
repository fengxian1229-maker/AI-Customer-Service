import hashlib
import json
from typing import Any

from app.workflows.command_contracts import CommandType


def build_sop_command(
    command_type: CommandType,
    state: dict[str, Any],
    intent: str,
    slot_memory: dict[str, Any],
    supplement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    platform = state.get("platform") or (state.get("payload_json") or {}).get("platform") or slot_memory.get("platform")
    livechat_group_id = (
        state.get("livechat_group_id")
        or (state.get("payload_json") or {}).get("livechat_group_id")
        or slot_memory.get("livechat_group_id")
    )
    payload: dict[str, Any] = {
        "intent": intent,
        "active_workflow": intent,
        "conversation_id": state.get("conversation_id"),
        "chat_id": state.get("chat_id"),
        "thread_id": state.get("thread_id"),
        "inbound_event_id": state.get("inbound_event_id"),
        "platform": platform,
        "livechat_group_id": livechat_group_id,
        "slot_memory": dict(slot_memory),
    }
    if command_type == CommandType.TELEGRAM_APPEND_TO_CASE:
        payload.update(
            {
                "telegram_case_id": slot_memory.get("telegram_case_id"),
                "telegram_message_id": slot_memory.get("telegram_message_id"),
                "telegram_target_chat_id": slot_memory.get("telegram_target_chat_id"),
                "telegram_message_thread_id": slot_memory.get("telegram_message_thread_id"),
                "supplement": supplement or {},
            }
        )
    return {"type": command_type, "payload": payload}


def command_idempotency_key(state: dict[str, Any], command_type: str) -> str:
    inbound_event_id = state.get("inbound_event_id")
    if inbound_event_id:
        return f"{inbound_event_id}:{command_type}"
    raw = json.dumps(
        {
            "conversation_id": state.get("conversation_id"),
            "text": state.get("raw_user_input") or state.get("rewritten_question") or "",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"{state.get('conversation_id')}:{command_type}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
