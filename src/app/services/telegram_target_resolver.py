from typing import Any


def resolve_telegram_target(command: dict[str, Any], settings) -> dict[str, Any]:
    payload = command.get("payload_json") or command.get("payload") or {}
    explicit_chat_id = payload.get("telegram_target_chat_id") or payload.get("target_chat_id")
    explicit_thread_id = payload.get("telegram_message_thread_id") or payload.get("message_thread_id")
    if explicit_chat_id:
        return {"chat_id": str(explicit_chat_id), "message_thread_id": explicit_thread_id, "target_source": "command_payload"}
    if getattr(settings, "telegram_test_group", None):
        return {"chat_id": str(settings.telegram_test_group), "message_thread_id": None, "target_source": "test_group"}
    if getattr(settings, "telegram_sop_target_chat_id", None):
        thread_id = None if getattr(settings, "telegram_force_no_topic", False) else getattr(settings, "telegram_sop_message_thread_id", None)
        return {"chat_id": str(settings.telegram_sop_target_chat_id), "message_thread_id": thread_id, "target_source": "explicit_sop_target"}
    if getattr(settings, "telegram_finance_group", None):
        return {"chat_id": str(settings.telegram_finance_group), "message_thread_id": None, "target_source": "finance_group"}
    return {"chat_id": None, "message_thread_id": None, "target_source": "missing"}
